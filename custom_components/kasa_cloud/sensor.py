"""Sensor platform for TP-Link Kasa Cloud energy monitoring and diagnostics."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KasaConfigEntry
from .entity import KasaCloudEntity

PARALLEL_UPDATES = 0  # read-only


def _as_float(value: object) -> float | None:
    """Coerce a cloud-supplied reading, or ``None`` if it is not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scaled(data: dict, milli_key: str, plain_key: str, digits: int) -> float | None:
    """Read a value that firmware reports either in milli-units or units."""
    milli = _as_float(data.get(milli_key))
    if milli is not None:
        return round(milli / 1000, digits)
    plain = _as_float(data.get(plain_key))
    if plain is not None:
        return round(plain, digits)
    return None


def _power(device: Any) -> float | None:
    return _scaled(device.emeter_realtime, "power_mw", "power", 1)


def _voltage(device: Any) -> float | None:
    return _scaled(device.emeter_realtime, "voltage_mv", "voltage", 1)


def _current(device: Any) -> float | None:
    return _scaled(device.emeter_realtime, "current_ma", "current", 3)


def _total_energy(device: Any) -> float | None:
    return _scaled(device.emeter_realtime, "total_wh", "total", 3)


@dataclass(frozen=True, kw_only=True)
class KasaSensorEntityDescription(SensorEntityDescription):
    """Describes a Kasa cloud sensor."""

    value_fn: Callable[[Any], float | int | None]


EMETER_SENSORS: tuple[KasaSensorEntityDescription, ...] = (
    KasaSensorEntityDescription(
        key="power",
        translation_key="current_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_power,
    ),
    KasaSensorEntityDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_voltage,
    ),
    KasaSensorEntityDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_current,
    ),
    KasaSensorEntityDescription(
        key="total_energy",
        translation_key="total_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_total_energy,
    ),
)

DIAGNOSTIC_SENSORS: tuple[KasaSensorEntityDescription, ...] = (
    KasaSensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda device: device.rssi,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KasaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa sensor entities."""
    coordinator = config_entry.runtime_data

    entities: list[SensorEntity] = []
    for device in coordinator.data.values():
        if not device.sys_info:
            continue  # see the note in switch.py
        if device.has_emeter:
            if device.has_children:
                # An HS300 meters each outlet separately.
                for child in device.children:
                    entities.extend(
                        KasaSensor(coordinator, child, description, parent=device)
                        for description in EMETER_SENSORS
                    )
            else:
                entities.extend(
                    KasaSensor(coordinator, device, description)
                    for description in EMETER_SENSORS
                )

        entities.extend(
            KasaSensor(coordinator, device, description)
            for description in DIAGNOSTIC_SENSORS
        )

    async_add_entities(entities)


class KasaSensor(KasaCloudEntity, SensorEntity):
    """A Kasa cloud sensor."""

    entity_description: KasaSensorEntityDescription

    def __init__(self, coordinator, device, description, parent=None) -> None:
        super().__init__(coordinator, device, key=description.key, parent=parent)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        """Return the reading, or ``None`` when the device has not reported it.

        No blanket ``except Exception`` here: upstream used one, which turned a
        missing ``emeter_realtime`` attribute into four permanently blank
        sensors that never logged anything.
        """
        return self.entity_description.value_fn(self._device)
