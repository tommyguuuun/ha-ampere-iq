"""User-entered key and per-installation UUID selection."""

import re
from contextlib import nullcontext

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ApiAuthError, ApiConnectionError, ApiResponseError, EkdApi
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_EQUIPMENT,
    CONF_INSTALLATION_UUID,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_UNIT_ID,
    CONF_POLL_INTERVAL,
    CONF_PV_SOURCES,
    CONF_PV_TOPOLOGY,
    CONNECTION_MODBUS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PV_SOURCES,
    DOMAIN,
    EQUIPMENT,
    MODBUS_EQUIPMENT,
    PV_TOPOLOGY_MULTIPLE,
    PV_TOPOLOGY_SINGLE,
)
from .safety import (
    initial_modbus_probe_lock,
    legacy_e3_present,
    modbus_entry_exists,
    modbus_entry_setup_lock,
    watch_modbus_conflicts,
)

API_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
MODBUS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODBUS_HOST): str,
        vol.Required(CONF_MODBUS_PORT, default=502): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_MODBUS_UNIT_ID, default=247): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
    }
)


def validated_modbus_endpoint(user_input):
    """Normalize and validate a host without an unserializable form validator."""
    endpoint = MODBUS_SCHEMA(user_input)
    host = endpoint[CONF_MODBUS_HOST].strip()
    if not 1 <= len(host) <= 255:
        raise vol.Invalid("Invalid Modbus host")
    endpoint[CONF_MODBUS_HOST] = host
    return endpoint


EQUIPMENT_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            {"value": "inverter", "label": "Wechselrichter"},
            {"value": "battery", "label": "Batterie"},
            {"value": "heat_pump", "label": "Wärmepumpe"},
            {"value": "wallbox", "label": "Wallbox"},
        ],
        multiple=True,
    )
)


PV_TOPOLOGY_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=[
        {"value": PV_TOPOLOGY_SINGLE, "label": "Ein E3-Wechselrichter"},
        {"value": PV_TOPOLOGY_MULTIPLE, "label": "Mehrere Erzeugungsquellen"},
    ])
)
PV_SOURCE_SELECTOR = SelectSelector(SelectSelectorConfig(options=[
    {"value": "mppt1", "label": "E3 MPPT 1"},
    {"value": "mppt2", "label": "E3 MPPT 2"},
    {"value": "meter2", "label": "Externer PV-Meterkanal (Meter 2)"},
], multiple=True))


def topology_schema(default=PV_TOPOLOGY_SINGLE):
    """Ask how the local PV installation is built before selecting sources."""
    return vol.Schema({vol.Required(CONF_PV_TOPOLOGY, default=default): PV_TOPOLOGY_SELECTOR})


def existing_topology(options):
    """Infer pre-wizard local entries from the actual saved source selection."""
    if options.get(CONF_PV_TOPOLOGY) in (PV_TOPOLOGY_SINGLE, PV_TOPOLOGY_MULTIPLE):
        return options[CONF_PV_TOPOLOGY]
    return (PV_TOPOLOGY_SINGLE
            if tuple(options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES)) == DEFAULT_PV_SOURCES
            else PV_TOPOLOGY_MULTIPLE)


def equipment_schema(
    equipment=None, interval=DEFAULT_POLL_INTERVAL, *, allowed=EQUIPMENT,
    pv_sources=None, topology=PV_TOPOLOGY_SINGLE,
):
    """Selection schema shared by first setup and edits."""
    selector = SelectSelector(
        SelectSelectorConfig(
            options=[item for item in EQUIPMENT_SELECTOR.config["options"]
                     if item["value"] in allowed],
            multiple=True,
        )
    )
    fields = {
        vol.Required(CONF_EQUIPMENT, default=list(allowed if equipment is None else equipment)):
            selector,
        vol.Required(CONF_POLL_INTERVAL, default=interval): vol.All(
            vol.Coerce(int), vol.Range(min=10 if allowed == MODBUS_EQUIPMENT else 30,
                                      max=3600)
        ),
    }
    if allowed == MODBUS_EQUIPMENT and topology == PV_TOPOLOGY_MULTIPLE:
        selected = pv_sources if pv_sources and "e3_total" not in pv_sources else (
            "mppt1", "mppt2", "meter2"
        )
        fields[vol.Required(CONF_PV_SOURCES, default=list(selected))] = PV_SOURCE_SELECTOR
    return vol.Schema(fields)


def validated_options(user_input, *, allowed=EQUIPMENT, topology=PV_TOPOLOGY_SINGLE):
    """Validate direct flow calls as well as submissions from the UI."""
    if topology not in (PV_TOPOLOGY_SINGLE, PV_TOPOLOGY_MULTIPLE):
        raise vol.Invalid("Unsupported PV topology")
    if (allowed == MODBUS_EQUIPMENT and topology == PV_TOPOLOGY_SINGLE
            and CONF_PV_SOURCES in user_input):
        raise vol.Invalid("Single inverter sources are automatic")
    options = equipment_schema(allowed=allowed, topology=topology)(user_input)
    selected = options[CONF_EQUIPMENT]
    if (not selected or len(selected) != len(set(selected))
            or any(item not in allowed for item in selected)):
        raise vol.Invalid("Choose at least one supported equipment type")
    if allowed == MODBUS_EQUIPMENT:
        if topology == PV_TOPOLOGY_MULTIPLE:
            pv_sources = options[CONF_PV_SOURCES]
            if (not pv_sources or len(set(pv_sources)) != len(pv_sources)
                    or any(source not in ("mppt1", "mppt2", "meter2")
                           for source in pv_sources)):
                raise vol.Invalid("Choose nonoverlapping PV sources")
        else:
            options[CONF_PV_SOURCES] = list(DEFAULT_PV_SOURCES)
        options[CONF_PV_TOPOLOGY] = topology
    return options


def e3_serial(identity: dict) -> str | None:
    """Require a usable E3 identity before creating or moving a config entry."""
    if not isinstance(identity, dict):
        return None
    serial = identity.get("serial")
    model = identity.get("model")
    if (
        not isinstance(serial, str)
        or not serial.strip()
        or not isinstance(model, str)
        or re.fullmatch(r"ASP-[0-9]+KW-[0-9]+P-[A-Z]-E3", model.upper()) is None
    ):
        return None
    return serial


class EkdConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Discover accessible installations without exposing credentials."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._installations: list[str] = []
        self._uuid: str | None = None
        self._modbus_settings: dict | None = None
        self._pv_topology: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Expose editable equipment and cadence on existing entries."""
        return EkdOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Choose a connection without touching either external endpoint."""
        if user_input is None:
            return self.async_show_menu(step_id="user", menu_options=["modbus", "cloud"])
        # Preserve direct calls from older config flows and test fixtures.
        return await self.async_step_cloud(user_input)

    async def async_step_cloud(self, user_input=None):
        """Configure the existing Ampere.IQ cloud path."""
        errors = {}
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            try:
                installations = await EkdApi(
                    async_get_clientsession(self.hass), key
                ).installations()
            except ApiAuthError:
                errors["base"] = "invalid_auth"
            except ApiConnectionError:
                errors["base"] = "cannot_connect"
            except ApiResponseError:
                errors["base"] = "invalid_response"
            else:
                self._api_key = key
                self._installations = installations
                if len(installations) == 1:
                    self._uuid = installations[0]
                    return await self.async_step_equipment()
                return await self.async_step_installation()
        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    async def async_step_modbus(self, user_input=None):
        """Collect a local endpoint; never connect just to render a form."""
        errors = {}
        if user_input is not None:
            try:
                endpoint = validated_modbus_endpoint(user_input)
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_options"
            else:
                async with initial_modbus_probe_lock(self.hass):
                    if legacy_e3_present(self.hass) or modbus_entry_exists(self.hass):
                        errors["base"] = "modbus_conflict"
                    else:
                        from .modbus import ModbusConnectionError, ModbusReader, ModbusReadError

                        client = ModbusReader(
                            endpoint[CONF_MODBUS_HOST],
                            endpoint[CONF_MODBUS_PORT],
                            endpoint[CONF_MODBUS_UNIT_ID],
                            can_read=lambda: (
                                not legacy_e3_present(self.hass)
                                and not modbus_entry_exists(self.hass)
                            ),
                        )
                        with watch_modbus_conflicts(self.hass, client):
                            try:
                                identity = await client.probe_identity()
                            except ModbusConnectionError:
                                errors["base"] = (
                                    "modbus_conflict" if (
                                        legacy_e3_present(self.hass)
                                        or modbus_entry_exists(self.hass)
                                    ) else "cannot_connect"
                                )
                            except ModbusReadError:
                                errors["base"] = "invalid_response"
                            else:
                                serial = e3_serial(identity)
                                if legacy_e3_present(self.hass) or modbus_entry_exists(self.hass):
                                    errors["base"] = "modbus_conflict"
                                elif serial is None:
                                    errors["base"] = "invalid_response"
                                else:
                                    self._modbus_settings = endpoint
                                    self._uuid = f"modbus_{serial}"
                                    return await self.async_step_topology()
                            finally:
                                await client.close()
        return self.async_show_form(step_id="modbus", data_schema=MODBUS_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Change the endpoint without changing the physical device or options."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_MODBUS:
            return self.async_abort(reason="not_supported")
        if user_input is not None:
            async with modbus_entry_setup_lock(self.hass, entry):
                return await self._async_step_reconfigure_locked(entry, user_input)
        return await self._async_step_reconfigure_locked(entry, user_input)

    async def _async_step_reconfigure_locked(self, entry, user_input):
        """Probe and update while no setup of this entry can run."""

        def setup_conflict() -> bool:
            return (
                legacy_e3_present(self.hass)
                or modbus_entry_exists(self.hass, exclude_entry=entry)
                or getattr(entry, "state", None)
                == config_entries.ConfigEntryState.SETUP_IN_PROGRESS
            )

        errors = {}
        if user_input is not None:
            try:
                endpoint = validated_modbus_endpoint(user_input)
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_options"
            else:
                if setup_conflict():
                    errors["base"] = "modbus_conflict"
                else:
                    from .modbus import ModbusConnectionError, ModbusReader, ModbusReadError

                    active_reader = getattr(getattr(entry, "runtime_data", None), "reader", None)
                    exclusive = (
                        active_reader.exclusive_probe()
                        if active_reader is not None else nullcontext()
                    )
                    reconfigure_ok = False
                    async with exclusive:
                        if setup_conflict():
                            errors["base"] = "modbus_conflict"
                        else:
                            client = ModbusReader(
                                endpoint[CONF_MODBUS_HOST],
                                endpoint[CONF_MODBUS_PORT],
                                endpoint[CONF_MODBUS_UNIT_ID],
                                can_read=lambda: not setup_conflict(),
                            )
                            with watch_modbus_conflicts(
                                self.hass, client, exclude_entry=entry
                            ):
                                try:
                                    identity = await client.probe_identity()
                                except ModbusConnectionError:
                                    errors["base"] = (
                                        "modbus_conflict" if setup_conflict()
                                        else "cannot_connect"
                                    )
                                except ModbusReadError:
                                    errors["base"] = "invalid_response"
                                else:
                                    serial = e3_serial(identity)
                                    if setup_conflict():
                                        errors["base"] = "modbus_conflict"
                                    elif serial is None:
                                        errors["base"] = "invalid_response"
                                    elif f"modbus_{serial}" != entry.unique_id:
                                        errors["base"] = "different_device"
                                    else:
                                        reconfigure_ok = True
                                finally:
                                    await client.close()
                    if reconfigure_ok:
                        if setup_conflict():
                            errors["base"] = "modbus_conflict"
                        else:
                            return self.async_update_reload_and_abort(
                                entry, data_updates=endpoint
                            )
        defaults = entry.data
        schema = vol.Schema(
            {
                vol.Required(CONF_MODBUS_HOST, default=defaults[CONF_MODBUS_HOST]): str,
                vol.Required(CONF_MODBUS_PORT, default=defaults[CONF_MODBUS_PORT]): int,
                vol.Required(CONF_MODBUS_UNIT_ID, default=defaults[CONF_MODBUS_UNIT_ID]): int,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    async def async_step_installation(self, user_input=None):
        if not self._installations or not self._api_key:
            return self.async_abort(reason="invalid_installation")
        errors = {}
        if user_input is not None:
            uuid = user_input[CONF_INSTALLATION_UUID]
            if uuid in self._installations:
                self._uuid = uuid
                return await self.async_step_equipment()
            errors["base"] = "invalid_installation"
        return self.async_show_form(
            step_id="installation",
            data_schema=vol.Schema(
                {vol.Required(CONF_INSTALLATION_UUID): vol.In(self._installations)}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        """Ask for a replacement API key after a rejected request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ensure the replacement key still grants access to this UUID."""
        errors = {}
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            try:
                installations = await EkdApi(
                    async_get_clientsession(self.hass), key
                ).installations()
            except ApiAuthError:
                errors["base"] = "invalid_auth"
            except ApiConnectionError:
                errors["base"] = "cannot_connect"
            except ApiResponseError:
                errors["base"] = "invalid_response"
            else:
                entry = self._get_reauth_entry()
                if entry.data[CONF_INSTALLATION_UUID] not in installations:
                    errors["base"] = "invalid_installation"
                else:
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_API_KEY: key}
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    async def async_step_topology(self, user_input=None):
        """Choose one E3 total or a sum of distinct generation inputs."""
        if not self._modbus_settings or not self._uuid:
            return self.async_abort(reason="invalid_installation")
        errors = {}
        if user_input is not None:
            try:
                topology = topology_schema()(user_input)[CONF_PV_TOPOLOGY]
            except (vol.Invalid, ValueError, TypeError, KeyError):
                errors["base"] = "invalid_options"
            else:
                self._pv_topology = topology
                return await self.async_step_equipment()
        return self.async_show_form(step_id="topology", data_schema=topology_schema(),
                                    errors=errors)

    async def async_step_equipment(self, user_input=None):
        """Choose supported categories without querying undocumented endpoints."""
        if not self._uuid or not (self._api_key or self._modbus_settings):
            return self.async_abort(reason="invalid_installation")
        allowed = MODBUS_EQUIPMENT if self._modbus_settings else EQUIPMENT
        errors = {}
        if user_input is not None:
            try:
                options = validated_options(user_input, allowed=allowed,
                                            topology=self._pv_topology or PV_TOPOLOGY_SINGLE)
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_options"
            else:
                if self._modbus_settings and (
                    legacy_e3_present(self.hass) or modbus_entry_exists(self.hass)
                ):
                    errors["base"] = "modbus_conflict"
                else:
                    return await self._async_create_installation(options)
        return self.async_show_form(
            step_id="equipment", data_schema=equipment_schema(allowed=allowed,
                                         topology=self._pv_topology or PV_TOPOLOGY_SINGLE),
            errors=errors
        )

    async def _async_create_installation(self, options):
        await self.async_set_unique_id(self._uuid)
        self._abort_if_unique_id_configured()
        if self._modbus_settings:
            if legacy_e3_present(self.hass) or modbus_entry_exists(self.hass):
                return self.async_show_form(
                    step_id="equipment",
                    data_schema=equipment_schema(allowed=MODBUS_EQUIPMENT,
                                                 topology=self._pv_topology or PV_TOPOLOGY_SINGLE),
                    errors={"base": "modbus_conflict"},
                )
            return self.async_create_entry(
                title="Ampere.IQ StoragePro E3",
                data={
                    CONF_CONNECTION_TYPE: CONNECTION_MODBUS,
                    CONF_INSTALLATION_UUID: self._uuid,
                    **self._modbus_settings,
                },
                options=options,
            )
        return self.async_create_entry(
            title=f"Ampere.IQ {self._uuid[:8]}",
            data={CONF_API_KEY: self._api_key, CONF_INSTALLATION_UUID: self._uuid},
            options=options,
        )


class EkdOptionsFlow(config_entries.OptionsFlowWithReload):
    """Apply equipment, layout, and polling changes by reloading this entry."""

    async def async_step_init(self, user_input=None):
        if self.config_entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_MODBUS:
            return await self.async_step_equipment(user_input)
        existing = self.config_entry.options
        errors = {}
        if user_input is not None:
            try:
                self._pv_topology = topology_schema(existing_topology(existing))(
                    user_input
                )[CONF_PV_TOPOLOGY]
            except (vol.Invalid, ValueError, TypeError, KeyError):
                errors["base"] = "invalid_options"
            else:
                return await self.async_step_equipment()
        return self.async_show_form(
            step_id="topology", data_schema=topology_schema(existing_topology(existing)),
            errors=errors,
        )

    async def async_step_equipment(self, user_input=None):
        existing = self.config_entry.options
        local = self.config_entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_MODBUS
        allowed = MODBUS_EQUIPMENT if local else EQUIPMENT
        topology = getattr(self, "_pv_topology", None) or existing_topology(existing)
        errors = {}
        if user_input is not None:
            try:
                options = validated_options(user_input, allowed=allowed, topology=topology)
            except (vol.Invalid, ValueError, TypeError, KeyError):
                errors["base"] = "invalid_options"
            else:
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="equipment" if local else "init",
            data_schema=equipment_schema(
                existing.get(CONF_EQUIPMENT, allowed),
                existing.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                allowed=allowed,
                pv_sources=existing.get(CONF_PV_SOURCES),
                topology=topology,
            ),
            errors=errors,
        )
