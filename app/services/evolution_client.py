from __future__ import annotations

import os
import re
from typing import Any

import httpx


def normalize_whatsapp_recipient(raw_target: str) -> str:
    """
    Normaliza JID ou telefone para envio na Evolution API v2.
    Mantém @lid ou @s.whatsapp.net se já vier como JID completo; caso contrário extrai dígitos.
    """
    cleaned = raw_target.strip()
    if "@" in cleaned:
        return cleaned
    digits = re.sub(r"\D", "", cleaned)
    return digits or cleaned


class EvolutionAPIClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 25.0,
    ) -> None:
        resolved_url = (base_url or os.getenv("EVOLUTION_API_URL") or "http://192.168.0.9:8080").strip()
        resolved_key = (api_key or os.getenv("EVOLUTION_API_KEY") or "").strip()
        if not resolved_key:
            raise RuntimeError(
                "EVOLUTION_API_KEY não configurada na instância WhatsApp nem no arquivo .env."
            )
        self.base_url = resolved_url.rstrip("/")
        self.api_key = resolved_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    async def send_text(
        self,
        instance_name: str,
        number: str,
        text: str,
        delay_ms: int = 1200,
    ) -> dict[str, Any]:
        recipient = normalize_whatsapp_recipient(number)
        url = f"{self.base_url}/message/sendText/{instance_name}"
        payload = {
            "number": recipient,
            "text": text,
            "delay": max(0, int(delay_ms)),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Evolution API sendText falhou ({resp.status_code}): {resp.text[:400]}"
                )
            return resp.json() if resp.content else {"status": "sent"}

    async def send_media(
        self,
        instance_name: str,
        number: str,
        media_base64: str,
        file_name: str,
        caption: str = "",
        mediatype: str = "document",
        mimetype: str = "application/pdf",
        delay_ms: int = 1000,
    ) -> dict[str, Any]:
        recipient = normalize_whatsapp_recipient(number)
        url = f"{self.base_url}/message/sendMedia/{instance_name}"
        payload = {
            "number": recipient,
            "mediatype": mediatype,
            "mimetype": mimetype,
            "caption": caption,
            "media": media_base64,
            "fileName": file_name,
            "delay": max(0, int(delay_ms)),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Evolution API sendMedia falhou ({resp.status_code}): {resp.text[:400]}"
                )
            return resp.json() if resp.content else {"status": "sent"}

    async def send_composing_presence(
        self,
        instance_name: str,
        number: str,
        delay_ms: int = 1500,
    ) -> None:
        recipient = normalize_whatsapp_recipient(number)
        url = f"{self.base_url}/chat/sendPresence/{instance_name}"
        payload = {
            "number": recipient,
            "delay": delay_ms,
            "presence": "composing",
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(url, json=payload, headers=self._headers())
        except Exception:
            return

    async def get_connection_state(self, instance_name: str) -> dict[str, Any]:
        url = f"{self.base_url}/instance/connectionState/{instance_name}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Evolution API connectionState falhou ({resp.status_code}): {resp.text[:400]}"
                )
            return resp.json()

    async def get_connect_qr(self, instance_name: str) -> dict[str, Any]:
        url = f"{self.base_url}/instance/connect/{instance_name}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Evolution API connect falhou ({resp.status_code}): {resp.text[:400]}"
                )
            return resp.json()

    async def configure_webhook(
        self,
        instance_name: str,
        webhook_url: str,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/webhook/set/{instance_name}"
        v2_payload = {
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "webhookByEvents": False,
                "webhookBase64": False,
                "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
            }
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=v2_payload, headers=self._headers())
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Evolution API webhook/set falhou ({resp.status_code}): {resp.text[:400]}"
                )
            return resp.json() if resp.content else {"status": "configured"}
