"""The service surface, one service per use case, against the real ledger.

Calls are made the way `ServiceRegistry.async_call` makes them: the data is validated by
the schema the service registered, wrapped in a real `ServiceCall`, and handed to the
registered handler. What lands in SQLite afterwards is what the test believes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
import voluptuous as vol
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.filament_ledger.const import (
    DOMAIN,
    SERVICE_ADJUST_SPOOL,
    SERVICE_DISCARD_FILAMENT,
    SERVICE_MOUNT_SPOOL,
    SERVICE_RECONCILE_SPOOL,
    SERVICE_REGISTER_SPOOL,
    SERVICE_UNMOUNT_SPOOL,
)
from custom_components.filament_ledger.domain.error import SpoolDiscardedError
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.spool_state import SpoolState
from custom_components.filament_ledger.infrastructure.ha.services import (
    async_register_services,
)

from .conftest import FakeHass, Harness, a_spool, as_hass


@dataclass
class ServiceGateway:
    """Calls a registered service the way the real registry does: validate against the
    declared schema, wrap in a real `ServiceCall`, invoke the handler."""

    hass: FakeHass

    def parse(self, service: str, data: dict[str, object]) -> dict[str, object]:
        _handler, schema = self.hass.services.registered[(DOMAIN, service)]
        return cast("dict[str, object]", schema(data))

    async def call(self, service: str, **data: object) -> None:
        handler, schema = self.hass.services.registered[(DOMAIN, service)]
        validated = schema(dict(data))
        await handler(ServiceCall(as_hass(self.hass), DOMAIN, service, validated))


@pytest.fixture
def services(harness: Harness) -> ServiceGateway:
    async_register_services(as_hass(harness.hass))
    return ServiceGateway(hass=harness.hass)


class TestRegistration:
    def test_every_documented_service_is_registered(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        assert {name for _domain, name in harness.hass.services.registered} == {
            SERVICE_REGISTER_SPOOL,
            SERVICE_RECONCILE_SPOOL,
            SERVICE_DISCARD_FILAMENT,
            SERVICE_ADJUST_SPOOL,
            SERVICE_MOUNT_SPOOL,
            SERVICE_UNMOUNT_SPOOL,
        }

    def test_registering_twice_changes_nothing(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        """Setup runs again on every reload; the guard keeps the first registration."""
        before = dict(harness.hass.services.registered)
        async_register_services(as_hass(harness.hass))
        assert harness.hass.services.registered == before


class TestSchemas:
    @pytest.mark.parametrize(
        ("service", "data"),
        [
            pytest.param(
                SERVICE_REGISTER_SPOOL,
                {"colour": "000000", "opening_weight": 1000},
                id="register-without-a-material",
            ),
            pytest.param(
                SERVICE_REGISTER_SPOOL,
                {"material": "WOOD", "colour": "000000", "opening_weight": 1000},
                id="register-with-an-unknown-material",
            ),
            pytest.param(
                SERVICE_REGISTER_SPOOL,
                {"material": "PLA", "colour": "000000"},
                id="register-unweighed",
            ),
            pytest.param(
                SERVICE_RECONCILE_SPOOL, {"spool_id": "s"}, id="reconcile-without-a-reading"
            ),
            pytest.param(
                SERVICE_DISCARD_FILAMENT,
                {"spool_id": "s", "mode": "whole_spool"},
                id="discard-without-a-reason",
            ),
            pytest.param(
                SERVICE_DISCARD_FILAMENT,
                {"spool_id": "s", "mode": "halfway", "reason": "r"},
                id="discard-with-an-unknown-mode",
            ),
            pytest.param(
                SERVICE_ADJUST_SPOOL,
                {"spool_id": "s", "reason": "r"},
                id="adjust-without-an-amount",
            ),
            pytest.param(SERVICE_MOUNT_SPOOL, {"spool_id": "s"}, id="mount-without-a-slot"),
            pytest.param(
                SERVICE_MOUNT_SPOOL, {"spool_id": "s", "slot": 9}, id="mount-past-the-last-slot"
            ),
            pytest.param(SERVICE_UNMOUNT_SPOOL, {}, id="unmount-without-a-spool-id"),
        ],
    )
    def test_malformed_data_never_reaches_a_use_case(
        self, services: ServiceGateway, service: str, data: dict[str, object]
    ) -> None:
        with pytest.raises(vol.Invalid):
            services.parse(service, data)

    def test_the_schema_fills_the_documented_defaults(self, services: ServiceGateway) -> None:
        registered = services.parse(
            SERVICE_REGISTER_SPOOL,
            {"material": "PLA", "colour": "000000", "opening_weight": 1000},
        )
        assert registered["confirm_duplicate_tag"] is False

        reconciled = services.parse(SERVICE_RECONCILE_SPOOL, {"spool_id": "s", "measured_g": 100})
        # A kitchen scale weighs the whole spool, so that is the default reading.
        assert reconciled["includes_core"] is True


class TestEachServiceReachesTheLedger:
    async def test_register_spool_writes_the_opening_movement(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        await services.call(
            SERVICE_REGISTER_SPOOL,
            material="PLA",
            colour="8323FF",
            opening_weight=750,
            core_weight=200,
            label="Galaxy Purple",
        )

        (summary,) = await harness.ledger.use_cases.queries.overview()
        assert summary.spool.label == "Galaxy Purple"
        assert summary.balance == Grams.of(750)
        assert summary.state is SpoolState.SEALED
        detail = await harness.ledger.use_cases.queries.detail(summary.spool.id)
        assert [line.movement.type.value for line in detail.lines] == ["OPENING_BALANCE"]

    async def test_the_configured_core_weight_fills_in_when_omitted(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        await services.call(
            SERVICE_REGISTER_SPOOL, material="PLA", colour="000000", opening_weight=1000
        )
        (summary,) = await harness.ledger.use_cases.queries.overview()
        assert summary.spool.core_weight == Grams.of(250)

    async def test_reconcile_spool_records_what_the_scale_said(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger)

        await services.call(SERVICE_RECONCILE_SPOOL, spool_id=spool_id, measured_g=1224)

        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(974)
        assert detail.lines[0].movement.type.value == "RECONCILIATION"

    async def test_discard_filament_writes_off_the_whole_spool(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger)

        await services.call(
            SERVICE_DISCARD_FILAMENT, spool_id=spool_id, mode="whole_spool", reason="water damage"
        )

        assert await harness.ledger.use_cases.queries.overview() == []
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.state is SpoolState.DISCARDED
        assert detail.summary.balance == Grams.zero()

    async def test_adjust_spool_moves_the_balance_by_a_movement(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger)

        await services.call(
            SERVICE_ADJUST_SPOOL, spool_id=spool_id, amount_g=-162, reason="lamp_shade"
        )

        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(838)
        assert detail.lines[0].movement.type.value == "MANUAL_ADJUSTMENT"

    async def test_mount_and_unmount_move_the_spool_without_a_movement(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger)

        await services.call(SERVICE_MOUNT_SPOOL, spool_id=spool_id, slot=3)
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.spool.location.__class__.__name__ == "AmsSlot"
        assert len(detail.lines) == 1  # moving a spool consumes no filament

        await services.call(SERVICE_UNMOUNT_SPOOL, spool_id=spool_id)
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.spool.location.__class__.__name__ == "Storage"

    async def test_every_service_refreshes_the_entities(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        await services.call(
            SERVICE_REGISTER_SPOOL, material="PLA", colour="000000", opening_weight=1000
        )
        assert harness.coordinator.refresh_count == 1


class TestRefusalsBecomeHomeAssistantErrors:
    """`_translated_errors`: a refused rule deserves a message, not a stack trace."""

    async def test_adjusting_a_discarded_spool_reads_as_a_message(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger)
        await services.call(
            SERVICE_DISCARD_FILAMENT, spool_id=spool_id, mode="whole_spool", reason="gone"
        )

        with pytest.raises(HomeAssistantError, match="discarded") as refusal:
            await services.call(SERVICE_ADJUST_SPOOL, spool_id=spool_id, amount_g=-1, reason="no")

        # The domain's refusal travels along as the cause, so nothing is lost in translation.
        assert isinstance(refusal.value.__cause__, SpoolDiscardedError)

    async def test_an_unknown_spool_is_reported_not_invented(
        self, services: ServiceGateway
    ) -> None:
        with pytest.raises(HomeAssistantError, match="no spool"):
            await services.call(SERVICE_RECONCILE_SPOOL, spool_id="nope", measured_g=100)

    async def test_agreement_with_the_scale_is_a_refusal_too(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger)
        with pytest.raises(HomeAssistantError, match="agrees"):
            await services.call(SERVICE_RECONCILE_SPOOL, spool_id=spool_id, measured_g=1250)

    async def test_without_a_set_up_ledger_every_service_refuses(
        self, services: ServiceGateway, harness: Harness
    ) -> None:
        harness.hass.config_entries.loaded.clear()
        with pytest.raises(HomeAssistantError, match="not set up"):
            await services.call(SERVICE_UNMOUNT_SPOOL, spool_id="any")
