#!/usr/bin/env python3
"""
Corrige a assinatura do hook de despacho WhatsApp (batch_size) em
app/services/automation_service.py, reinicia a API, executa a suíte
End-to-End completa (9 testes) e reinicia o worker systemd.

Salve em: ~/apps/comercial-saas/fix_evolution_dispatch_hook.py
Execute com: python3 ~/apps/comercial-saas/fix_evolution_dispatch_hook.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

MARKER = "# --- EVOLUTION_BRIDGE_DISPATCH_HOOK ---"

HOOK_BLOCK = f"""{MARKER}
_orig_process_dispatch_queue = process_dispatch_queue


async def process_dispatch_queue(
    session,
    tenant_id,
    batch_size: int = 50,
    *args,
    **kwargs,
):
    from app.services import evolution_bridge_service

    effective_limit = int(kwargs.get("limit", batch_size))
    await evolution_bridge_service.dispatch_pending_whatsapp_via_evolution(
        session, tenant_id, limit=effective_limit
    )
    return await _orig_process_dispatch_queue(
        session, tenant_id, batch_size=batch_size, *args, **kwargs
    )
"""


def patch_automation_hook(project_dir: Path) -> None:
    auto_srv = project_dir / "app" / "services" / "automation_service.py"
    if not auto_srv.exists():
        raise SystemExit(f"Arquivo não encontrado: {auto_srv}")

    content = auto_srv.read_text(encoding="utf-8")
    idx = content.find(MARKER)
    base_content = content[:idx].rstrip() if idx != -1 else content.rstrip()
    auto_srv.write_text(base_content + "\n\n\n" + HOOK_BLOCK, encoding="utf-8")
    print("[OK] Hook process_dispatch_queue atualizado com batch_size=50 em app/services/automation_service.py")


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    patch_automation_hook(project_dir)

    print("[INFO] Reiniciando container api...")
    subprocess.run(["docker", "compose", "restart", "api"], cwd=str(project_dir), check=True)
    time.sleep(3)

    print("[INFO] Executando suíte End-to-End (9 testes)...")
    subprocess.run(
        ["python3", "manage_comercial_saas.py", "smoke-test"],
        cwd=str(project_dir),
        check=True,
    )

    print("[INFO] Reiniciando worker systemd...")
    subprocess.run(
        ["sudo", "systemctl", "restart", "comercial-saas-worker.service"],
        cwd=str(project_dir),
        check=False,
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERRO] Comando falhou ({exc.cmd}) com código {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode)