from datetime import datetime
import random
from bson.json_util import dumps, loads


from mies.buildings.stats import decrement_bldgs, UNPROCESSED, \
    PROCESSED, increment_bldgs, BEING_PROCESSED

from mies.constants import DEFAULT_BLDG_ENERGY
from mies.buildings.model import logging, ONE_DAY_IN_SECONDS
from mies.celery import app
from mies.mongo_config import get_db
from mies.redis_config import get_cache
from mies.senses.smell.smell_propagator import propagate_smell


def update_action_status(bldg, action_status):
    # TODO have a Bldg class & move the method there
    actions = bldg.get("actions", [])
    actions[-1] = action_status
    db = get_db()
    db.buildings.update({
        "_id": bldg["_id"]
    }, {
        "$set": {
            "actions": actions
        }
    })
    logging.info("Updated bldg {} action status: {}".format(
        bldg["address"], action_status))


def add_new_action_status(bldg, action_status):
    # TODO have a Bldg class & move the method there
    actions = bldg.get("actions", [])
    actions.append(action_status)
    db = get_db()
    db.buildings.update({
        "_id": bldg["_id"]
    }, {
        "$set": {
            "actions": actions
        }
    })
    logging.info("Added new action status to bldg {}: {}".format(
        bldg["address"], action_status))


def update_bldg_processed_status(bldg, energy_change, output_bldgs=None):
    # TODO have a Bldg class & move the method there
    was_processed = bldg["processed"]
    is_processed = (energy_change < 0)

    curr_bldg_energy = bldg["energy"] or DEFAULT_BLDG_ENERGY
    new_energy = curr_bldg_energy + energy_change
    change = {
        "processed": is_processed,
        "energy": new_energy
    }
    if output_bldgs:
        change["outputs"] = output_bldgs
    db = get_db()
    db.buildings.update({
                            "_id": bldg["_id"]
                        }, {
                            "$set": change
                        })
    # also update the cache
    cache = get_cache()
    cached_bldg_json = cache.get(bldg["address"])
    cached_bldg = None
    try:
        cached_bldg = loads(cached_bldg_json)
        cached_bldg.update(change)
        cache.set(bldg["address"], dumps(cached_bldg), ex=ONE_DAY_IN_SECONDS)
    except:
        logging.exception("couldn't update cache")
        logging.error("there's {} in the cache at {}".format(
            str(cached_bldg_json), bldg["address"]
        ))

    propagate_smell(bldg["address"], new_energy)
    if not was_processed and is_processed:
        decrement_bldgs(bldg["flr"], UNPROCESSED)
        increment_bldgs(bldg["flr"], PROCESSED)
        # TODO handle also a case of unprocess
    logging.info("Updated bldg {} processed status: {}".format(
        bldg["address"], change))


def update_bldg_with_results(bldg, content_type, summary_payload,
                             raw_payload, result_payload,
                             cache_period=ONE_DAY_IN_SECONDS):
    # TODO have a Bldg class & move the method there
    change = {}
    if content_type and content_type != bldg["contentType"]:
        change["contentType"] = content_type
    if summary_payload is not None:
        bldg["summary"].update(summary_payload)
        change["summary"] = bldg["summary"]
    if result_payload is not None:
        bldg["payload"].update(result_payload)
        change["payload"] = bldg["payload"]

    db = get_db()
    db.buildings.update({
                            "_id": bldg["_id"]
                        }, {
                            "$set": change
                        })
    # if raw payload also changed, update it in cache
    if raw_payload is not None:
        bldg["raw"] = raw_payload
    cache = get_cache()
    cache.set(bldg["address"], dumps(bldg), ex=cache_period)
    logging.info("Updated bldg {} with results".format(bldg["address"]))


class ActingBehavior:

    def update_processing_status(self, is_processing, energy_gained=0):
        self.processing = is_processing
        self.energy = self.energy + energy_gained

    def finish_processing(self, action_status, bldg, output_bldgs=None):
        bldg_energy = bldg["energy"] or DEFAULT_BLDG_ENERGY
        #success = action_status["successLevel"]
        # TODO figure out the success level & store in action status
        success = 1
        energy_gained = bldg_energy * success
        self.update_processing_status(False, energy_gained)
        update_bldg_processed_status(bldg, -energy_gained, output_bldgs)
        decrement_bldgs(bldg["flr"], BEING_PROCESSED)

    def get_latest_action(self, bldg):
        """
        Get the most recent action logged in the given bldg.
        TODO move to Bldg class
        """
        if not bldg["actions"]:
            return None
        return bldg["actions"][-1]

    def is_action_pending(self, action_status):
        """
        TODO move to Bldg class
        """
        return "endedAt" not in action_status

    def should_discard_action(self, action_status):
        """
        Action should be discarded in case:
        * it didn't complete in 24h
        * its result is ERROR
        :param action_status:
        :return:
        """
        if (datetime.utcnow() - action_status["startedAt"]).days > 0 or \
           (datetime.utcnow() - action_status["startedAt"]).seconds > 60 * 60:
            return True
        # TODO How come there wasn't a result key???
        if action_status.get("result") == "ERROR":
            return True
        return False

    def discard_action(self, bldg, action_status):
        action_status["endedAt"] = datetime.utcnow()
        action_status["status"] = "DISCARDED"
        update_action_status(bldg, action_status)

    def get_registered_actions(self, content_type):
        """
        Stub implementation
        TODO lookup actions registered for given content-type
        """
        registered_actions = {
            "twitter-social-post": ["fetch-article"],
            "article-text": ["extract-article-concepts"],
        }
        return registered_actions.get(content_type)

    def choose_action(self, bldg):
        """
        Stub implementation.
        TODO lookup registered actions by content-type
        TODO extract features from bldg payload
        TODO predict the success of each action
        TODO return the action with highest predicted success
        """
        registered_actions = self.get_registered_actions(bldg["contentType"])
        return random.choice(registered_actions)

    def mark_as_executing(self):
        # mark resident as processing
        self.update_processing_status(True)

    def start_processing(self, action, bldg):
        if bldg.get("raw") is None:
            # TODO increment metric
            logging.warning("Invoking actions but couldn't find raw payload, "
                            "using result payload instead")
        payload = bldg["payload"]
        if "raw" in bldg:
            payload.update(bldg["raw"])
        if "summary" in bldg:
            payload.update(bldg["summary"])
        task = app.send_task(action, [payload], queue="actions")
        action_status = {
            "startedAt": datetime.utcnow(),
            "startedBy": self._id,
            "action": action,
            "result_id": task.task_id
        }
        add_new_action_status(bldg, action_status)
        self.mark_as_executing()
        increment_bldgs(bldg["flr"], BEING_PROCESSED)

    def get_action_result(self, action_status):
        result_id = action_status["result_id"]
        result = app.AsyncResult(result_id)
        if result.ready():
            try:
                return result.get(timeout=1)
            except:
                pass
        return None
