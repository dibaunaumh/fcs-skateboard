from datetime import datetime
import random
from mies.buildings.constants import DEFAULT_BLDG_ENERGY
from mies.mongoconfig import get_db


def update_action_status(bldg, action_status):
    # TODO have a Bldg class & move the method there
    actions = bldg["actions"]
    actions[-1] = action_status
    db = get_db()
    db.buildings.update({
        "_id": bldg["_id"]
    }, {
        "$set": {
            "actions": actions
        }
    })


def add_new_action_status(bldg, action_status):
    # TODO have a Bldg class & move the method there
    actions = bldg["actions"]
    actions.append(action_status)
    db = get_db()
    db.buildings.update({
        "_id": bldg["_id"]
    }, {
        "$set": {
            "actions": actions
        }
    })


def update_bldg_processed_status(bldg, energy_change):
    # TODO have a Bldg class & move the method there
    curr_bldg_energy = bldg["energy"] or DEFAULT_BLDG_ENERGY
    db = get_db()
    db.buildings.update({
                            "_id": bldg["_id"]
                        }, {
                            "$set": {
                                "processed": (energy_change < 0),
                                "energy": curr_bldg_energy + energy_change
                            }
                        })


class ActingBehavior:

    def update_processing_status(self, is_processing, energy_gained=0):
        db = get_db()
        db.residents.update({
                                "_id": self._id
                            }, {
                                "$set": {
                                    "processing": is_processing,
                                    "energy": self.energy + energy_gained
                                }
                            })

    def finish_processing(self, action_status, bldg):
        bldg_energy = bldg["energy"] or DEFAULT_BLDG_ENERGY
        success = action_status["successLevel"]
        energy_gained = bldg_energy * success
        self.update_processing_status(False, energy_gained)
        update_bldg_processed_status(bldg, -energy_gained)

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
        if (datetime.utcnow() - action_status["startedAt"]).seconds > 60 * 60 * 24:
            return True
        if action_status["result"] == "ERROR":
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
            "twitter-social-post": ["fetch-article"]
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

    def mark_as_executing(self, action, bldg):
        # mark resident as processing
        action_status = {
            "startedAt": datetime.utcnow(),
            "startedBy": self._id,
            "action": action
        }
        add_new_action_status(bldg, action_status)
        self.update_processing_status(True)

    def start_action(self, action, bldg):
        pass
