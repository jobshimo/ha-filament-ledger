"""Durable registration retry receipts, committed with the opening movement."""

from dataclasses import dataclass
from datetime import datetime

from ...domain.port.repositories import RegistrationReceipt
from ...domain.value.identifiers import SpoolId
from .database import Database


@dataclass(frozen=True, slots=True)
class SqliteRegistrationRequestRepository:
    database: Database

    async def get(self, actor_id: str, request_id: str) -> RegistrationReceipt | None:
        row = await self.database.fetch_one(
            "SELECT * FROM registration_request WHERE actor_id = ? AND request_id = ?",
            (actor_id, request_id),
        )
        return (
            RegistrationReceipt(
                row["actor_id"],
                row["request_id"],
                row["payload_fingerprint"],
                SpoolId(row["spool_id"]),
                datetime.fromisoformat(row["created_at"]),
            )
            if row
            else None
        )

    async def save(self, receipt: RegistrationReceipt) -> None:
        await self.database.execute(
            "INSERT INTO registration_request VALUES (?, ?, ?, ?, ?)",
            (
                receipt.actor_id,
                receipt.request_id,
                receipt.payload_fingerprint,
                receipt.spool_id,
                receipt.created_at.isoformat(),
            ),
        )
