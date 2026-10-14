from enum import Enum
from .common import Contract


class Scenario(str, Enum):
    RESET = "reset"          # baseline data, no incident
    CALM = "calm"            # scene 1: all green
    WARNING = "warning"      # scene 2: EQ-3012 vibration ramps, ML risk rises
    CRISIS = "crisis"        # scenes 3-5: incident INC-0042 opened, agents run
    RESOLVED = "resolved"    # scenes 7-8: work order done, feedback stored
    FULL_STORY = "full_story"  # plays calm -> warning -> crisis automatically


class ScenarioResponse(Contract):
    scenario: Scenario
    started: bool
    message: str
