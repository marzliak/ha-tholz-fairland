"""Fairland heat-pump enum values."""

from enum import IntEnum


class HEATING_TYPE(IntEnum):
    """Supported heating type."""

    TROCADOR_CALOR_FAIRLAND = 7


class HEATING_OP_MODE(IntEnum):
    """Supported operating modes."""

    DESLIGADO = 0
    AUTOMATICO = 2
    AQUECER = 4
    RESFRIAR = 5


class HEATING_FAN_MODE(IntEnum):
    """Supported fan modes."""

    SILENCIOSO = 0
    INTELIGENTE = 1
