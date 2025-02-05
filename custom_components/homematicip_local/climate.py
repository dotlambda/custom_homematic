"""climate for Homematic(IP) Local."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import logging
from typing import Any, Final

from hahomematic.const import HmPlatform
from hahomematic.platforms.custom.climate import (
    PRESET_MODE_PREFIX,
    BaseClimateEntity,
    HvacAction as HmHvacAction,
    HvacMode as HmHvacMode,
    PresetMode,
)
import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_PRESET_MODE,
    ATTR_TEMPERATURE,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HomematicConfigEntry
from .const import (
    SERVICE_DISABLE_AWAY_MODE,
    SERVICE_ENABLE_AWAY_MODE_BY_CALENDAR,
    SERVICE_ENABLE_AWAY_MODE_BY_DURATION,
)
from .control_unit import ControlUnit, signal_new_hm_entity
from .generic_entity import HaHomematicGenericRestoreEntity

_LOGGER = logging.getLogger(__name__)

ATTR_AWAY_END: Final = "end"
ATTR_AWAY_HOURS: Final = "hours"
ATTR_AWAY_TEMPERATURE: Final = "away_temperature"
ATTR_AWAY_START: Final = "start"

SUPPORTED_HA_PRESET_MODES: Final = [
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
]

HM_TO_HA_HVAC_MODE: Mapping[HmHvacMode, HVACMode] = {
    HmHvacMode.AUTO: HVACMode.AUTO,
    HmHvacMode.COOL: HVACMode.COOL,
    HmHvacMode.HEAT: HVACMode.HEAT,
    HmHvacMode.OFF: HVACMode.OFF,
}

HA_TO_HM_HVAC_MODE: Mapping[HVACMode, HmHvacMode] = {v: k for k, v in HM_TO_HA_HVAC_MODE.items()}

HM_TO_HA_ACTION: Mapping[HmHvacAction, HVACAction] = {
    HmHvacAction.COOL: HVACAction.COOLING,
    HmHvacAction.HEAT: HVACAction.HEATING,
    HmHvacAction.IDLE: HVACAction.IDLE,
    HmHvacAction.OFF: HVACAction.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomematicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Homematic(IP) Local climate platform."""
    control_unit: ControlUnit = entry.runtime_data

    @callback
    def async_add_climate(hm_entities: tuple[BaseClimateEntity, ...]) -> None:
        """Add climate from Homematic(IP) Local."""
        _LOGGER.debug("ASYNC_ADD_CLIMATE: Adding %i entities", len(hm_entities))

        if entities := [
            HaHomematicClimate(
                control_unit=control_unit,
                hm_entity=hm_entity,
            )
            for hm_entity in hm_entities
        ]:
            async_add_entities(entities)

    entry.async_on_unload(
        func=async_dispatcher_connect(
            hass=hass,
            signal=signal_new_hm_entity(entry_id=entry.entry_id, platform=HmPlatform.CLIMATE),
            target=async_add_climate,
        )
    )

    async_add_climate(hm_entities=control_unit.get_new_entities(entity_type=BaseClimateEntity))

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_ENABLE_AWAY_MODE_BY_CALENDAR,
        {
            vol.Optional(ATTR_AWAY_START): cv.datetime,
            vol.Required(ATTR_AWAY_END): cv.datetime,
            vol.Required(ATTR_AWAY_TEMPERATURE, default=18.0): vol.All(
                vol.Coerce(float), vol.Range(min=5.0, max=30.5)
            ),
        },
        "async_enable_away_mode_by_calendar",
    )
    platform.async_register_entity_service(
        SERVICE_ENABLE_AWAY_MODE_BY_DURATION,
        {
            vol.Required(ATTR_AWAY_HOURS): cv.positive_int,
            vol.Required(ATTR_AWAY_TEMPERATURE, default=18.0): vol.All(
                vol.Coerce(float), vol.Range(min=5.0, max=30.5)
            ),
        },
        "async_enable_away_mode_by_duration",
    )
    platform.async_register_entity_service(
        SERVICE_DISABLE_AWAY_MODE,
        {},
        "async_disable_away_mode",
    )


class HaHomematicClimate(HaHomematicGenericRestoreEntity[BaseClimateEntity], ClimateEntity):
    """Representation of the HomematicIP climate entity."""

    _attr_translation_key = "hmip_climate"
    _enable_turn_on_off_backwards_compatibility: bool = False

    def __init__(
        self,
        control_unit: ControlUnit,
        hm_entity: BaseClimateEntity,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(
            control_unit=control_unit,
            hm_entity=hm_entity,
        )
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_target_temperature_step = hm_entity.target_temperature_step

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if self._hm_entity.is_valid:
            return self._hm_entity.target_temperature
        if self.is_restored and self._restored_state:
            return self._restored_state.attributes.get(ATTR_TEMPERATURE)
        return None

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if self._hm_entity.is_valid:
            return self._hm_entity.current_temperature
        if self.is_restored and self._restored_state:
            return self._restored_state.attributes.get(ATTR_CURRENT_TEMPERATURE)
        return None

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        if self._hm_entity.is_valid:
            return self._hm_entity.current_humidity
        if self.is_restored and self._restored_state:
            return self._restored_state.attributes.get(ATTR_CURRENT_HUMIDITY)
        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the hvac action."""
        if self._hm_entity.hvac_action and self._hm_entity.hvac_action in HM_TO_HA_ACTION:
            return HM_TO_HA_ACTION[self._hm_entity.hvac_action]
        return None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac mode."""
        if self._hm_entity.is_valid:
            if self._hm_entity.hvac_mode in HM_TO_HA_HVAC_MODE:
                return HM_TO_HA_HVAC_MODE[self._hm_entity.hvac_mode]
            return HVACMode.OFF
        if (
            self.is_restored
            and self._restored_state
            and (restored_state := self._restored_state.state)
            not in (
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            )
        ):
            return HVACMode(value=restored_state)
        return None

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available hvac modes."""
        return [
            HM_TO_HA_HVAC_MODE[hm_hvac_mode]
            for hm_hvac_mode in self._hm_entity.hvac_modes
            if hm_hvac_mode in HM_TO_HA_HVAC_MODE
        ]

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._hm_entity.min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._hm_entity.max_temp

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if (
            self._hm_entity.is_valid
            and self._hm_entity.preset_mode in SUPPORTED_HA_PRESET_MODES
            or str(self._hm_entity.preset_mode).startswith(PRESET_MODE_PREFIX)
        ):
            return self._hm_entity.preset_mode
        if self.is_restored and self._restored_state:
            return self._restored_state.attributes.get(ATTR_PRESET_MODE)
        return None

    @property
    def preset_modes(self) -> list[str]:
        """Return a list of available preset modes incl. hmip profiles."""
        preset_modes = []
        for hm_preset_mode in self._hm_entity.preset_modes:
            if hm_preset_mode in SUPPORTED_HA_PRESET_MODES:
                preset_modes.append(hm_preset_mode.value)
            if str(hm_preset_mode).startswith(PRESET_MODE_PREFIX):
                preset_modes.append(hm_preset_mode.value)
        return preset_modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        if self._hm_entity.supports_preset:
            supported_features |= ClimateEntityFeature.PRESET_MODE
        return supported_features

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._hm_entity.set_temperature(temperature=temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode not in HA_TO_HM_HVAC_MODE:
            _LOGGER.warning("Hvac mode %s is not supported by integration", hvac_mode)
            return
        await self._hm_entity.set_hvac_mode(HA_TO_HM_HVAC_MODE[hvac_mode])

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in self.preset_modes:
            _LOGGER.warning(
                "Preset mode %s is not supported in hvac_mode %s",
                preset_mode,
                self.hvac_mode,
            )
            return
        await self._hm_entity.set_preset_mode(PresetMode(preset_mode))

    async def async_enable_away_mode_by_calendar(
        self,
        end: datetime,
        away_temperature: float,
        start: datetime | None = None,
    ) -> None:
        """Enable the away mode by calendar on thermostat."""
        start = start or datetime.now() - timedelta(minutes=10)
        await self._hm_entity.enable_away_mode_by_calendar(
            start=start, end=end, away_temperature=away_temperature
        )

    async def async_enable_away_mode_by_duration(
        self, hours: int, away_temperature: float
    ) -> None:
        """Enable the away mode by duration on thermostat."""
        await self._hm_entity.enable_away_mode_by_duration(
            hours=hours, away_temperature=away_temperature
        )

    async def async_disable_away_mode(self) -> None:
        """Disable the away mode on thermostat."""
        await self._hm_entity.disable_away_mode()
