"""Suíte automatizada de validação End-to-End (Módulos 5 a 12) executável via python -m app.workers.smoke_test."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from typing import Any

from sqlalchemy import text

from app.api.deps import TenantContext, get_tenant_context
from app.db.session import get_db_session as _db_session_dep
from app.main import app


def build_tenant_context(tenant_id: uuid.UUID) -> TenantContext:
    try:
        return TenantContext(tenant_id=tenant_id)
    except Exception:
        sig = inspect.signature(TenantContext)
        kwargs: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name == "tenant_id":
                kwargs[name] = tenant_id
            elif param.default is inspect.Parameter.empty:
                if "id" in name:
                    kwargs[name] = uuid.uuid4()
                elif "role" in name:
                    kwargs[name] = "ADMIN"
                elif "scope" in name or "permission" in name:
                    kwargs[name] = ["*"]
                else:
                    kwargs[name] = "smoke-test"
        return TenantContext(**kwargs)


async def get_or_create_smoke_tenant() -> uuid.UUID:
    gen = _db_session_dep()
    session = await gen.__anext__()
    try:
        res = await session.execute(text("SELECT id FROM tenants LIMIT 1"))
        row = res.first()
        if row and row[0]:
            return uuid.UUID(str(row[0]))

        new_id = uuid.uuid4()
        cols_res = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tenants'"
            )
        )
        cols = {r[0] for r in cols_res.fetchall()}
        insert_cols = ["id"]
        insert_vals: dict[str, Any] = {"id": new_id}

        if "name" in cols:
            insert_cols.append("name")
            insert_vals["name"] = "TBTC Smoke Test Tenant"
        if "slug" in cols:
            insert_cols.append("slug")
            insert_vals["slug"] = f"tbtc-smoke-{new_id.hex[:6]}"
        if "is_active" in cols:
            insert_cols.append("is_active")
            insert_vals["is_active"] = True

        placeholders = ", ".join(f":{c}" for c in insert_cols)
        sql = text(f"INSERT INTO tenants ({', '.join(insert_cols)}) VALUES ({placeholders})")
        await session.execute(sql, insert_vals)
        await session.commit()
        return new_id
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass


async def asgi_request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    raw_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
    if json_body is not None:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(raw_body)).encode("ascii")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    body_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    status_code = 500
    chunks: list[bytes] = []

    async def send(message: dict[str, Any]) -> None:
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    raw_resp = b"".join(chunks).decode("utf-8", errors="replace")
    try:
        return status_code, json.loads(raw_resp)
    except Exception:
        return status_code, raw_resp


async def run_smoke_suite() -> None:
    tenant_id = await get_or_create_smoke_tenant()
    print(f"=== Iniciando Smoke Test End-to-End Completo (Tenant: {tenant_id}) ===")

    app.dependency_overrides[get_tenant_context] = lambda: build_tenant_context(tenant_id)

    try:
        code, data = await asgi_request("GET", "/openapi.json")
        assert code == 200, f"OpenAPI falhou ({code}): {data}"
        paths = data.get("paths", {})
        print(f"  [PASS] 1. OpenAPI carregado ({len(paths)} rotas registradas)")

        code, sub_data = await asgi_request("GET", "/api/v1/saas/subscription")
        assert code == 200, f"Subscription falhou ({code}): {sub_data}"
        print(f"  [PASS] 2. Assinatura SaaS ativa (Plano: {sub_data['plan_tier']})")

        unique_mail = f"closer.{uuid.uuid4().hex[:6]}@tbtc.local"
        code, mem_data = await asgi_request(
            "POST",
            "/api/v1/team/members",
            json_body={
                "full_name": "Closer Smoke Test",
                "email": unique_mail,
                "role": "CLOSER",
                "monthly_revenue_quota": 25000.00,
                "monthly_deals_quota": 5,
            },
        )
        assert code == 201, f"Team member falhou ({code}): {mem_data}"
        print("  [PASS] 3. Membro comercial cadastrado no Módulo 8")

        code, inv = await asgi_request(
            "POST",
            "/api/v1/billing/invoices",
            json_body={
                "customer_name": "Cliente Validacao E2E Ltda",
                "customer_email": "financeiro@clientee2e.com.br",
                "description": "Implantacao Plataforma Comercial SaaS",
                "amount": 2500.00,
                "payment_method": "PIX",
                "closer_name": "Closer Smoke Test",
                "metadata_json": {"customer_phone": "5541999998888"},
            },
        )
        assert code == 201, f"Invoice falhou ({code}): {inv}"
        inv_num = inv["invoice_number"]
        print(f"  [PASS] 4. Fatura PIX {inv_num} emitida (R$ {inv['net_amount']})")

        code, prt = await asgi_request(
            "POST",
            "/api/v1/team/portal-links",
            json_body={
                "customer_name": "Cliente Validacao E2E Ltda",
                "invoice_id": inv["id"],
            },
        )
        assert code == 201, f"Portal link falhou ({code}): {prt}"
        token = prt["token"]
        code, pub_data = await asgi_request("GET", f"/api/v1/team/public/{token}")
        assert code == 200, f"Public portal falhou ({code}): {pub_data}"
        print("  [PASS] 5. Portal Self-Service do Cliente validado com token público")

        code, wh_res = await asgi_request(
            "POST",
            "/api/v1/saas/webhooks/public/pix-gateway",
            json_body={
                "event": "PAYMENT_CONFIRMED",
                "event_id": f"evt_{uuid.uuid4().hex[:12]}",
                "invoice_number": inv_num,
                "status": "PAID",
            },
        )
        assert code == 200, f"Webhook inbound falhou ({code}): {wh_res}"
        assert wh_res["settled_invoice_number"] == inv_num, wh_res
        assert wh_res["generated_commissions_count"] >= 1, wh_res
        print(
            f"  [PASS] 6. Webhook Inbound liquidou {inv_num}, gerou "
            f"{wh_res['generated_commissions_count']} comissão(ões) e acionou "
            f"{wh_res['enqueued_automations_count']} automação(ões)"
        )

        code, rep = await asgi_request("GET", "/api/v1/automation/executive-report")
        assert code == 200, f"Executive report falhou ({code}): {rep}"
        code, usage = await asgi_request("GET", "/api/v1/saas/usage")
        assert code == 200, f"SaaS usage falhou ({code}): {usage}"
        print(
            f"  [PASS] 7. Relatório Executivo OK (Caixa Líquido Pós-Comissão: "
            f"R$ {rep['net_cash_after_commissions']} | Disparos Enviados: {rep['dispatches_sent_count']})"
        )

        code, hm = await asgi_request("GET", "/api/v1/governance/health-matrix")
        assert code == 200 and hm["database_connected"] is True, f"Health matrix falhou: {hm}"
        code, csv_text = await asgi_request("GET", "/api/v1/governance/exports/invoices.csv")
        assert code == 200 and "numero_fatura" in str(csv_text), f"CSV export falhou ({code})"
        print(
            f"  [PASS] 8. Governança & CSV OK (DB Latência: {hm['database_latency_ms']} ms | "
            f"Faturas no banco: {hm['counters']['billing_invoices']})"
        )

        code, evo_cfg = await asgi_request("GET", "/api/v1/evolution/config")
        assert code == 200, f"Evolution config falhou ({code}): {evo_cfg}"
        print(
            f"  [PASS] 9. Conector Evolution API v2 OK (Instância: {evo_cfg['instance_name']} | "
            f"Ativo: {evo_cfg['is_enabled']})"
        )

        code, cad = await asgi_request("POST", "/api/v1/cadences/bootstrap-default")
        assert code == 201, f"Cadence bootstrap falhou ({code}): {cad}"
        code, enr = await asgi_request(
            "POST",
            f"/api/v1/cadences/{cad['id']}/enroll",
            json_body={
                "lead_name": "Ricardo Almeida",
                "company_name": "Metalurgica Sul Brasil",
                "email": "ricardo@metalurgicasul.com.br",
                "phone": "5541988887777",
                "role_title": "Diretor Comercial",
                "segment": "Industria Metalurgica",
                "context_notes": "empresa expandindo equipe externa no Parana",
                "start_immediately": True,
            },
        )
        assert code == 201, f"Cadence enroll falhou ({code}): {enr}"
        code, tick = await asgi_request("POST", "/api/v1/cadences/process-due")
        assert code == 200 and tick["dispatched_steps"] >= 1, f"Cadence tick falhou ({code}): {tick}"
        code, conv = await asgi_request(
            "POST",
            f"/api/v1/cadences/enrollments/{enr['id']}/convert-deal",
            json_body={"estimated_amount": 4800.0, "owner_name": "SDR IA"},
        )
        assert code == 200 and conv["enrollment_status"] == "CONVERTED", f"Convert deal falhou ({code}): {conv}"
        print(
            f"  [PASS] 10. Motor de Cadências D+0/D+2/D+5 & SDR IA OK "
            f"(Toques disparados: {tick['dispatched_steps']} | Deal CRM: {conv['deal_title']})"
        )

        print()
        print("[SUCESSO TOTAL] Todos os 10 testes End-to-End passaram sem erros!")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    asyncio.run(run_smoke_suite())
