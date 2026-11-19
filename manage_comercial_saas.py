#!/usr/bin/env python3
"""
Orquestrador Operacional de Produção do Comercial SaaS (Módulos 1 a 11).

Comandos disponíveis:
  python3 ~/apps/comercial-saas/manage_comercial_saas.py status
  python3 ~/apps/comercial-saas/manage_comercial_saas.py init-db
  python3 ~/apps/comercial-saas/manage_comercial_saas.py smoke-test
  python3 ~/apps/comercial-saas/manage_comercial_saas.py dunning
  python3 ~/apps/comercial-saas/manage_comercial_saas.py backup
  python3 ~/apps/comercial-saas/manage_comercial_saas.py install-systemd
"""

from __future__ import annotations

import argparse
import ast
import gzip
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = PROJECT_DIR / "backups"
SERVICE_NAME = "comercial-saas-worker.service"
SMOKE_MODULE_PATH = PROJECT_DIR / "app" / "workers" / "smoke_test.py"


def _run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_DIR),
        check=check,
        text=True,
        capture_output=capture,
    )


def _discover_session_dep() -> tuple[str, str]:
    """Detecta (module_path, func_name) da dependência de sessão usada nos routers."""
    v1_dir = PROJECT_DIR / "app" / "api" / "v1"
    for fname in ("evolution_bridge.py", "governance.py", "billing.py", "crm_deals.py"):
        fpath = v1_dir / fname
        if not fpath.exists():
            continue
        tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module != "app.api.deps":
                for alias in node.names:
                    if alias.name in ("get_db", "get_session", "get_async_session", "get_db_session"):
                        return node.module, alias.name
    return "app.db.session", "get_async_session"


def ensure_smoke_test_module() -> None:
    """Garante que app/workers/smoke_test.py exista dentro do volume montado /app/app."""
    mod_path, func_name = _discover_session_dep()
    SMOKE_MODULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_file = SMOKE_MODULE_PATH.parent / "__init__.py"
    if not init_file.exists():
        init_file.write_text('"""Workers e utilitários internos do Comercial SaaS."""\n', encoding="utf-8")

    code = f'''"""Suíte automatizada de validação End-to-End (Módulos 5 a 11) executável via python -m app.workers.smoke_test."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from typing import Any

from sqlalchemy import text

from app.api.deps import TenantContext, get_tenant_context
from {mod_path} import {func_name} as _db_session_dep
from app.main import app


def build_tenant_context(tenant_id: uuid.UUID) -> TenantContext:
    try:
        return TenantContext(tenant_id=tenant_id)
    except Exception:
        sig = inspect.signature(TenantContext)
        kwargs: dict[str, Any] = {{}}
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
        cols = {{r[0] for r in cols_res.fetchall()}}
        insert_cols = ["id"]
        insert_vals: dict[str, Any] = {{"id": new_id}}

        if "name" in cols:
            insert_cols.append("name")
            insert_vals["name"] = "TBTC Smoke Test Tenant"
        if "slug" in cols:
            insert_cols.append("slug")
            insert_vals["slug"] = f"tbtc-smoke-{{new_id.hex[:6]}}"
        if "is_active" in cols:
            insert_cols.append("is_active")
            insert_vals["is_active"] = True

        placeholders = ", ".join(f":{{c}}" for c in insert_cols)
        sql = text(f"INSERT INTO tenants ({{', '.join(insert_cols)}}) VALUES ({{placeholders}})")
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

    scope = {{
        "type": "http",
        "asgi": {{"version": "3.0"}},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }}

    body_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {{"type": "http.request", "body": raw_body, "more_body": False}}
        return {{"type": "http.disconnect"}}

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
    print(f"=== Iniciando Smoke Test End-to-End Completo (Tenant: {{tenant_id}}) ===")

    app.dependency_overrides[get_tenant_context] = lambda: build_tenant_context(tenant_id)

    try:
        code, data = await asgi_request("GET", "/openapi.json")
        assert code == 200, f"OpenAPI falhou ({{code}}): {{data}}"
        paths = data.get("paths", {{}})
        print(f"  [PASS] 1. OpenAPI carregado ({{len(paths)}} rotas registradas)")

        code, sub_data = await asgi_request("GET", "/api/v1/saas/subscription")
        assert code == 200, f"Subscription falhou ({{code}}): {{sub_data}}"
        print(f"  [PASS] 2. Assinatura SaaS ativa (Plano: {{sub_data['plan_tier']}})")

        unique_mail = f"closer.{{uuid.uuid4().hex[:6]}}@tbtc.local"
        code, mem_data = await asgi_request(
            "POST",
            "/api/v1/team/members",
            json_body={{
                "full_name": "Closer Smoke Test",
                "email": unique_mail,
                "role": "CLOSER",
                "monthly_revenue_quota": 25000.00,
                "monthly_deals_quota": 5,
            }},
        )
        assert code == 201, f"Team member falhou ({{code}}): {{mem_data}}"
        print("  [PASS] 3. Membro comercial cadastrado no Módulo 8")

        code, inv = await asgi_request(
            "POST",
            "/api/v1/billing/invoices",
            json_body={{
                "customer_name": "Cliente Validacao E2E Ltda",
                "customer_email": "financeiro@clientee2e.com.br",
                "description": "Implantacao Plataforma Comercial SaaS",
                "amount": 2500.00,
                "payment_method": "PIX",
                "closer_name": "Closer Smoke Test",
                "metadata_json": {{"customer_phone": "5541999998888"}},
            }},
        )
        assert code == 201, f"Invoice falhou ({{code}}): {{inv}}"
        inv_num = inv["invoice_number"]
        print(f"  [PASS] 4. Fatura PIX {{inv_num}} emitida (R$ {{inv['net_amount']}})")

        code, prt = await asgi_request(
            "POST",
            "/api/v1/team/portal-links",
            json_body={{
                "customer_name": "Cliente Validacao E2E Ltda",
                "invoice_id": inv["id"],
            }},
        )
        assert code == 201, f"Portal link falhou ({{code}}): {{prt}}"
        token = prt["token"]
        code, pub_data = await asgi_request("GET", f"/api/v1/team/public/{{token}}")
        assert code == 200, f"Public portal falhou ({{code}}): {{pub_data}}"
        print("  [PASS] 5. Portal Self-Service do Cliente validado com token público")

        code, wh_res = await asgi_request(
            "POST",
            "/api/v1/saas/webhooks/public/pix-gateway",
            json_body={{
                "event": "PAYMENT_CONFIRMED",
                "event_id": f"evt_{{uuid.uuid4().hex[:12]}}",
                "invoice_number": inv_num,
                "status": "PAID",
            }},
        )
        assert code == 200, f"Webhook inbound falhou ({{code}}): {{wh_res}}"
        assert wh_res["settled_invoice_number"] == inv_num, wh_res
        assert wh_res["generated_commissions_count"] >= 1, wh_res
        print(
            f"  [PASS] 6. Webhook Inbound liquidou {{inv_num}}, gerou "
            f"{{wh_res['generated_commissions_count']}} comissão(ões) e acionou "
            f"{{wh_res['enqueued_automations_count']}} automação(ões)"
        )

        code, rep = await asgi_request("GET", "/api/v1/automation/executive-report")
        assert code == 200, f"Executive report falhou ({{code}}): {{rep}}"
        code, usage = await asgi_request("GET", "/api/v1/saas/usage")
        assert code == 200, f"SaaS usage falhou ({{code}}): {{usage}}"
        print(
            f"  [PASS] 7. Relatório Executivo OK (Caixa Líquido Pós-Comissão: "
            f"R$ {{rep['net_cash_after_commissions']}} | Disparos Enviados: {{rep['dispatches_sent_count']}})"
        )

        code, hm = await asgi_request("GET", "/api/v1/governance/health-matrix")
        assert code == 200 and hm["database_connected"] is True, f"Health matrix falhou: {{hm}}"
        code, csv_text = await asgi_request("GET", "/api/v1/governance/exports/invoices.csv")
        assert code == 200 and "numero_fatura" in str(csv_text), f"CSV export falhou ({{code}})"
        print(
            f"  [PASS] 8. Governança & CSV OK (DB Latência: {{hm['database_latency_ms']}} ms | "
            f"Faturas no banco: {{hm['counters']['billing_invoices']}})"
        )

        code, evo_cfg = await asgi_request("GET", "/api/v1/evolution/config")
        assert code == 200, f"Evolution config falhou ({{code}}): {{evo_cfg}}"
        print(
            f"  [PASS] 9. Conector Evolution API v2 OK (Instância: {{evo_cfg['instance_name']}} | "
            f"Ativo: {{evo_cfg['is_enabled']}})"
        )

        print()
        print("[SUCESSO TOTAL] Todos os 9 testes End-to-End passaram sem erros!")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    asyncio.run(run_smoke_suite())
'''
    SMOKE_MODULE_PATH.write_text(code, encoding="utf-8")


def cmd_status() -> None:
    ensure_smoke_test_module()
    print("=== 1. Containers Docker Compose ===")
    _run(["docker", "compose", "ps"])

    print("\n=== 2. Healthcheck Interno da API & Banco ===")
    check_script = (
        "import asyncio\n"
        "from app.workers.smoke_test import asgi_request, get_or_create_smoke_tenant, build_tenant_context\n"
        "from app.api.deps import get_tenant_context\n"
        "from app.main import app\n"
        "async def main():\n"
        "    tid = await get_or_create_smoke_tenant()\n"
        "    c, d = await asgi_request('GET', '/openapi.json')\n"
        "    print(f'[OK] OpenAPI HTTP {c} | Rotas registradas: {len(d.get(\"paths\", {}))}')\n"
        "    app.dependency_overrides[get_tenant_context] = lambda: build_tenant_context(tid)\n"
        "    hc, hd = await asgi_request('GET', '/api/v1/governance/health-matrix')\n"
        "    app.dependency_overrides.clear()\n"
        "    print(f'[OK] Health Matrix HTTP {hc} | PostgreSQL Latencia: {hd.get(\"database_latency_ms\")} ms | Modulos: {len(hd.get(\"modules_status\", {}))}')\n"
        "asyncio.run(main())\n"
    )
    _run(["docker", "compose", "exec", "-T", "api", "python", "-c", check_script])


def cmd_init_db() -> None:
    print("=== Sincronizando tabelas dos Módulos 1 a 11 no PostgreSQL ===")
    init_script = (
        "import asyncio\n"
        "from app.db.base import Base\n"
        "from app.db.session import async_engine\n"
        "import app.models\n"
        "async def main():\n"
        "    async with async_engine.begin() as conn:\n"
        "        await conn.run_sync(Base.metadata.create_all)\n"
        "    print('[OK] Todas as tabelas (Módulos 1 a 11) verificadas/criadas no PostgreSQL.')\n"
        "asyncio.run(main())\n"
    )
    _run(["docker", "compose", "exec", "-T", "api", "python", "-c", init_script])


def cmd_smoke_test() -> None:
    ensure_smoke_test_module()
    print("=== Executando Suíte End-to-End (9 Testes) ===")
    _run(["docker", "compose", "exec", "-T", "api", "python", "-m", "app.workers.smoke_test"])


def cmd_dunning() -> None:
    ensure_smoke_test_module()
    print("=== Executando Varredura de Inadimplência & Fila Omnichannel ===")
    py_code = (
        "import asyncio\n"
        "from app.workers.smoke_test import get_or_create_smoke_tenant, _db_session_dep\n"
        "from app.services import automation_service\n"
        "async def main():\n"
        "    tid = await get_or_create_smoke_tenant()\n"
        "    gen = _db_session_dep()\n"
        "    s = await gen.__anext__()\n"
        "    res = await automation_service.scan_and_enqueue_overdue_invoices(s, tid)\n"
        "    await s.commit()\n"
        "    print(res.model_dump_json(indent=2))\n"
        "    await gen.aclose()\n"
        "asyncio.run(main())\n"
    )
    _run(["docker", "compose", "exec", "-T", "api", "python", "-c", py_code])


def cmd_backup(keep_last: int = 14) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = BACKUP_DIR / f"comercial_saas_{ts}.sql.gz"

    print(f"=== Gerando backup PostgreSQL em {out_file} ===")
    ps_out = _run(["docker", "compose", "ps", "--services"], capture=True).stdout.splitlines()
    pg_service = next((s for s in ps_out if s in ("db", "postgres") or "postgres" in s), "db")

    proc = subprocess.Popen(
        ["docker", "compose", "exec", "-T", pg_service, "sh", "-c", 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"'],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    with gzip.open(out_file, "wb") as gz:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            gz.write(chunk)

    stderr_txt = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    ret = proc.wait()
    if ret != 0:
        out_file.unlink(missing_ok=True)
        raise SystemExit(f"[ERRO] pg_dump falhou (código {ret}): {stderr_txt}")

    size_kb = round(out_file.stat().st_size / 1024, 2)
    print(f"  [OK] Backup salvo ({size_kb} KB)")

    backups = sorted(BACKUP_DIR.glob("comercial_saas_*.sql.gz"), reverse=True)
    for old_file in backups[keep_last:]:
        old_file.unlink(missing_ok=True)
        print(f"  [LIMPEZA] Backup antigo removido: {old_file.name}")


def cmd_install_systemd() -> None:
    user = os.getenv("USER", "server")
    unit_content = f"""[Unit]
Description=Comercial SaaS - Worker Assincrono Omnichannel & Cobrancas
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=simple
User={user}
WorkingDirectory={PROJECT_DIR}
ExecStart=/usr/bin/docker compose exec -T api python -m app.workers.automation_worker
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    tmp_unit = PROJECT_DIR / SERVICE_NAME
    tmp_unit.write_text(unit_content, encoding="utf-8")

    print(f"=== Instalando serviço systemd {SERVICE_NAME} ===")
    _run(["sudo", "cp", str(tmp_unit), f"/etc/systemd/system/{SERVICE_NAME}"])
    tmp_unit.unlink(missing_ok=True)
    _run(["sudo", "systemctl", "daemon-reload"])
    _run(["sudo", "systemctl", "enable", "--now", SERVICE_NAME])
    _run(["sudo", "systemctl", "status", SERVICE_NAME, "--no-pager", "-n", "12"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Orquestrador Operacional Comercial SaaS")
    parser.add_argument(
        "action",
        choices=["status", "init-db", "smoke-test", "dunning", "backup", "install-systemd"],
        help="Ação operacional a executar",
    )
    args = parser.parse_args()

    actions = {
        "status": cmd_status,
        "init-db": cmd_init_db,
        "smoke-test": cmd_smoke_test,
        "dunning": cmd_dunning,
        "backup": cmd_backup,
        "install-systemd": cmd_install_systemd,
    }
    actions[args.action]()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERRO] Falha ao executar comando ({exc.cmd}) com código {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode)