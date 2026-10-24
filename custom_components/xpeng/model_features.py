"""Explicit app model gates and verified P7+ additions; cloud overrides win."""

MODEL_FEATURES = {
    "DC": {
        "DC": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": False,
            "isSupportAirDeodorization": False,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": False,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": False,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": False,
            "isSupportSeatFrontWind": False,
        }
    },
    "DG": {
        "DG": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": False,
            "isSupportAirDeodorization": False,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": False,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": False,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": False,
            "isSupportSeatFrontWind": False,
        }
    },
    "EA": {
        "EA": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": True,
            "isSupportSeatFrontWind": True,
        }
    },
    "ED": {
        "ED": {
            "isSeatDoubleCheck": True,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": False,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": False,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": False,
            "isSupportSeatFrontWind": False,
        }
    },
    "EE": {
        "EE": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": True,
            "isSupportSeatFrontWind": True,
        }
    },
    "EF": {
        "EF": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": False,
            "isSupportSeatFrontWind": False,
        }
    },
    "FA": {
        "FA": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": True,
            "isSupportSeatFrontWind": False,
        }
    },
    "FF": {
        "FF": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": True,
            "isSupportSeatFrontWind": True,
        }
    },
    "FJ": {
        "FJ": {
            # P7+ cloud command + Android 3.16.0 toggle verified 2026-10-24.
            "isSupportSilentMode": True,
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": False,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": False,
            "isSupportSeatBackWind": False,
            "isSupportSeatFrontHeat": False,
            "isSupportSeatFrontWind": False,
        }
    },
    "HA": {
        "HA": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": True,
            "isSupportSeatBackWind": True,
            "isSupportSeatFrontHeat": True,
            "isSupportSeatFrontWind": True,
        }
    },
    "HE": {
        "HE": {
            "isSeatDoubleCheck": False,
            "isSupportAirClean": True,
            "isSupportAirDeodorization": True,
            "isSupportAirHighTemp": True,
            "isSupportChargePortControl": True,
            "isSupportFastCold": True,
            "isSupportFastWarm": True,
            "isSupportRearDeforest": True,
            "isSupportSeatBackHeat": True,
            "isSupportSeatBackWind": True,
            "isSupportSeatFrontHeat": True,
            "isSupportSeatFrontWind": True,
        }
    },
}
