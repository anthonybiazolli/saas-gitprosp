from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import HTTPException, status
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, create_model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import redis_client
from app.models.core import CustomFieldDefinition, CustomFieldEntity, CustomFieldType

CACHE_TTL_SECONDS = 300

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PHONE_REGEX = re.compile(r"^\+?[1-9]\d{7,14}$")
URL_REGEX = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def _validate_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for i in range(9, 11):
        total = sum(int(digits[num]) * ((i + 1) - num) for num in range(0, i))
        check = ((total * 10) % 11) % 10
        if check != int(digits[i]):
            return False
    return True


def _validate_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    weights_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    sum_1 = sum(int(digits[i]) * weights_1[i] for i in range(12))
    rem_1 = sum_1 % 11
    digit_1 = 0 if rem_1 < 2 else 11 - rem_1
    if digit_1 != int(digits[12]):
        return False

    sum_2 = sum(int(digits[i]) * weights_2[i] for i in range(13))
    rem_2 = sum_2 % 11
    digit_2 = 0 if rem_2 < 2 else 11 - rem_2
    return digit_2 == int(digits[13])


def validate_cpf_cnpj_value(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and _validate_cpf(digits):
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if len(digits) == 14 and _validate_cnpj(digits):
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    raise ValueError("CPF ou CNPJ inválido (dígitos verificadores incorretos).")


def validate_email_value(value: str) -> str:
    cleaned = value.strip().lower()
    if not EMAIL_REGEX.match(cleaned):
        raise ValueError("Formato de e-mail inválido.")
    return cleaned


def validate_phone_value(value: str) -> str:
    cleaned = re.sub(r"[\s().-]", "", value.strip())
    if not PHONE_REGEX.match(cleaned):
        raise ValueError("Telefone inválido. Utilize formato internacional (ex: +5541999999999).")
    return cleaned


def validate_url_value(value: str) -> str:
    cleaned = value.strip()
    if not URL_REGEX.match(cleaned):
        raise ValueError("URL inválida. Deve iniciar com http:// ou https://.")
    return cleaned


def _serialize_for_jsonb(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_serialize_for_jsonb(item) for item in value]
    if isinstance(value, dict):
        return {k: _serialize_for_jsonb(v) for k, v in value.items()}
    return value


class CustomFieldValidationService:
    @staticmethod
    def _cache_key(tenant_id: uuid.UUID, entity_type: CustomFieldEntity) -> str:
        return f"saas:cf_defs:{tenant_id}:{entity_type.value}"

    @classmethod
    async def invalidate_cache(
        cls, tenant_id: uuid.UUID, entity_type: CustomFieldEntity
    ) -> None:
        try:
            await redis_client.delete(cls._cache_key(tenant_id, entity_type))
        except Exception:
            pass

    @classmethod
    async def get_active_definitions(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        entity_type: CustomFieldEntity,
    ) -> list[dict[str, Any]]:
        cache_key = cls._cache_key(tenant_id, entity_type)
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

        stmt = (
            select(CustomFieldDefinition)
            .where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.entity_type == entity_type,
                CustomFieldDefinition.is_active.is_(True),
            )
            .order_by(CustomFieldDefinition.display_order.asc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

        serialized = [
            {
                "field_key": r.field_key,
                "label": r.label,
                "field_type": r.field_type.value,
                "options": r.options or [],
                "default_value": r.default_value,
                "validation_rules": r.validation_rules or {},
                "is_required": r.is_required,
            }
            for r in rows
        ]

        try:
            await redis_client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(serialized))
        except Exception:
            pass

        return serialized

    @classmethod
    def compile_pydantic_model(
        cls,
        definitions: list[dict[str, Any]],
        model_name: str = "DynamicCustomFieldsModel",
        allow_undefined_keys: bool = True,
    ) -> type[BaseModel]:
        field_definitions: dict[str, tuple[Any, Any]] = {}

        for defn in definitions:
            key: str = defn["field_key"]
            ftype = CustomFieldType(defn["field_type"])
            rules: dict[str, Any] = defn.get("validation_rules") or {}
            options: list[dict[str, Any]] = defn.get("options") or []
            is_required: bool = bool(defn.get("is_required", False))
            default_val: Any = defn.get("default_value")

            field_kwargs: dict[str, Any] = {
                "title": defn.get("label", key),
            }

            if ftype in (CustomFieldType.TEXT, CustomFieldType.TEXTAREA):
                if "min_length" in rules:
                    field_kwargs["min_length"] = int(rules["min_length"])
                if "max_length" in rules:
                    field_kwargs["max_length"] = int(rules["max_length"])
                if "regex" in rules and rules["regex"]:
                    field_kwargs["pattern"] = str(rules["regex"])
                py_type: Any = Annotated[str, Field(**field_kwargs)]

            elif ftype == CustomFieldType.NUMBER:
                if "min" in rules:
                    field_kwargs["ge"] = float(rules["min"])
                if "max" in rules:
                    field_kwargs["le"] = float(rules["max"])
                py_type = Annotated[float | int, Field(**field_kwargs)]

            elif ftype == CustomFieldType.CURRENCY:
                field_kwargs["ge"] = float(rules["min"]) if "min" in rules else 0.0
                if "max" in rules:
                    field_kwargs["le"] = float(rules["max"])
                py_type = Annotated[Decimal | float | int, Field(**field_kwargs)]

            elif ftype == CustomFieldType.PERCENTAGE:
                field_kwargs["ge"] = float(rules.get("min", 0.0))
                field_kwargs["le"] = float(rules.get("max", 100.0))
                py_type = Annotated[float | int, Field(**field_kwargs)]

            elif ftype == CustomFieldType.DATE:
                py_type = Annotated[date, Field(**field_kwargs)]

            elif ftype == CustomFieldType.DATETIME:
                py_type = Annotated[datetime, Field(**field_kwargs)]

            elif ftype == CustomFieldType.BOOLEAN:
                py_type = Annotated[bool, Field(**field_kwargs)]

            elif ftype == CustomFieldType.SELECT:
                allowed_vals = [str(opt["value"]) for opt in options if "value" in opt]

                def _make_select_validator(valid_options: list[str]):
                    def _check_select(v: str) -> str:
                        if valid_options and v not in valid_options:
                            raise ValueError(
                                f"Valor '{v}' inválido. Opções permitidas: {', '.join(valid_options)}."
                            )
                        return v

                    return _check_select

                py_type = Annotated[
                    str,
                    AfterValidator(_make_select_validator(allowed_vals)),
                    Field(**field_kwargs),
                ]

            elif ftype == CustomFieldType.MULTISELECT:
                allowed_vals = [str(opt["value"]) for opt in options if "value" in opt]

                def _make_multiselect_validator(valid_options: list[str]):
                    def _check_multiselect(values: list[str]) -> list[str]:
                        if valid_options:
                            invalid = [v for v in values if v not in valid_options]
                            if invalid:
                                raise ValueError(
                                    f"Opções inválidas: {', '.join(invalid)}. Permitidas: {', '.join(valid_options)}."
                                )
                        return values

                    return _check_multiselect

                py_type = Annotated[
                    list[str],
                    AfterValidator(_make_multiselect_validator(allowed_vals)),
                    Field(**field_kwargs),
                ]

            elif ftype == CustomFieldType.EMAIL:
                py_type = Annotated[str, AfterValidator(validate_email_value), Field(**field_kwargs)]

            elif ftype == CustomFieldType.PHONE:
                py_type = Annotated[str, AfterValidator(validate_phone_value), Field(**field_kwargs)]

            elif ftype == CustomFieldType.URL:
                py_type = Annotated[str, AfterValidator(validate_url_value), Field(**field_kwargs)]

            elif ftype == CustomFieldType.CNPJ_CPF:
                py_type = Annotated[str, AfterValidator(validate_cpf_cnpj_value), Field(**field_kwargs)]

            else:
                py_type = Any

            if is_required and default_val is None:
                field_definitions[key] = (py_type, ...)
            else:
                field_definitions[key] = (py_type | None, default_val)

        config = ConfigDict(
            extra="allow" if allow_undefined_keys else "forbid",
            str_strip_whitespace=True,
        )

        return create_model(
            model_name,
            __config__=config,
            **field_definitions,  # type: ignore[arg-type]
        )

    @classmethod
    async def validate_and_normalize(
        cls,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        entity_type: CustomFieldEntity,
        incoming_custom_fields: dict[str, Any] | None,
        existing_custom_fields: dict[str, Any] | None = None,
        allow_undefined_keys: bool = True,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if existing_custom_fields:
            merged.update(existing_custom_fields)
        if incoming_custom_fields:
            merged.update(incoming_custom_fields)

        definitions = await cls.get_active_definitions(session, tenant_id, entity_type)
        if not definitions:
            return _serialize_for_jsonb(merged)

        dynamic_model = cls.compile_pydantic_model(
            definitions=definitions,
            model_name=f"Dynamic_{entity_type.value.capitalize()}_CustomFields",
            allow_undefined_keys=allow_undefined_keys,
        )

        try:
            validated_instance = dynamic_model.model_validate(merged)
        except ValidationError as exc:
            formatted_errors = [
                {
                    "field": ".".join(str(loc) for loc in err["loc"]),
                    "message": err["msg"],
                    "type": err["type"],
                }
                for err in exc.errors()
            ]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": f"Falha na validação dos campos personalizados ({entity_type.value}).",
                    "errors": formatted_errors,
                },
            ) from exc

        dumped = validated_instance.model_dump(exclude_none=True)
        return _serialize_for_jsonb(dumped)
