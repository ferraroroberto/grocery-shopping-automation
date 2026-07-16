"""Inventory CRUD: read the list, nudge or set quantities, edit/add/delete a
row, and export the sheet as CSV. Every mutating route echoes back the full
inventory payload, so the PWA re-renders from one authoritative response."""

import csv
from io import StringIO
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api_common import (
    get_row,
    inventory_error,
    inventory_payload,
    load_inventory_or_error,
    mutate_or_error,
    save_or_error,
)
from src.data import (
    apply_item_edit,
    build_new_item_row,
    bulk_apply_tenemos,
    update_item_quantity,
    update_target_quantity,
)

router = APIRouter()


class DeltaPayload(BaseModel):
    delta: int = Field(..., ge=-1000, le=1000)


class QuantityPayload(BaseModel):
    value: int = Field(..., ge=0)


class ItemPayload(BaseModel):
    super_value: str = Field(..., alias="super")
    lugar: str
    comida: str
    cantidad: int = Field(..., ge=0)
    tenemos: int = Field(..., ge=0)
    buscador: str = ""


@router.get("/api/inventory")
def inventory() -> dict[str, Any]:
    df = load_inventory_or_error()
    return inventory_payload(df)


@router.post("/api/items/{item_id}/current-delta")
def current_delta(item_id: int, payload: DeltaPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    get_row(df, item_id)
    mutate_or_error(update_item_quantity, df, item_id, payload.delta)
    return inventory_payload(load_inventory_or_error())


@router.post("/api/items/{item_id}/target-delta")
def target_delta(item_id: int, payload: DeltaPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    get_row(df, item_id)
    mutate_or_error(update_target_quantity, df, item_id, payload.delta)
    return inventory_payload(load_inventory_or_error())


@router.post("/api/items/{item_id}/current")
def set_current(item_id: int, payload: QuantityPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    get_row(df, item_id)
    mutate_or_error(bulk_apply_tenemos, df, {item_id: payload.value}, save=True)
    return inventory_payload(load_inventory_or_error())


@router.put("/api/items/{item_id}")
def update_item(item_id: int, payload: ItemPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    snap = get_row(df, item_id).copy()
    apply_item_edit(
        df,
        item_id,
        super_value=payload.super_value,
        lugar=payload.lugar,
        comida=payload.comida,
        cantidad=payload.cantidad,
        tenemos=payload.tenemos,
        buscador=payload.buscador,
    )
    try:
        save_or_error(df)
    except HTTPException:
        df.loc[item_id] = snap
        raise
    return inventory_payload(load_inventory_or_error())


@router.delete("/api/items/{item_id}")
def delete_item(item_id: int) -> dict[str, Any]:
    df = load_inventory_or_error()
    get_row(df, item_id)
    df = df.drop(item_id)
    save_or_error(df)
    return inventory_payload(load_inventory_or_error())


@router.post("/api/items")
def add_item(payload: ItemPayload) -> dict[str, Any]:
    if not payload.comida.strip():
        raise inventory_error(400, "item name is required")
    df = load_inventory_or_error()
    new_row = build_new_item_row(
        super_value=payload.super_value,
        lugar=payload.lugar,
        comida=payload.comida,
        cantidad=payload.cantidad,
        tenemos=payload.tenemos,
        buscador=payload.buscador,
    )
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_or_error(df)
    return inventory_payload(load_inventory_or_error())


@router.get("/api/export.csv")
def export_csv() -> Response:
    df = load_inventory_or_error()
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(df.columns.tolist())
    for row in df.itertuples(index=False):
        writer.writerow(list(row))
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory.csv"'},
    )
