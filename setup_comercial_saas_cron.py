#!/usr/bin/env python3
"""
Configurador idempotente do agendamento automático (crontab) no servidor tbtc
para o Comercial SaaS:
  - Backup diário do PostgreSQL (03:00)
  - Régua de cobrança / varredura de faturas vencidas (08:30)

Salve em: ~/apps/comercial-saas/setup_comercial_saas_cron.py
Execute com: python3 ~/apps/comercial-saas/setup_comercial_saas_cron.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = PROJECT_DIR / "backups"
ORCHESTRATOR = PROJECT_DIR / "manage_comercial_saas.py"
CRON_LOG = BACKUP_DIR / "cron.log"
MARKER_BEGIN = "# BEGIN COMERCIAL_SAAS_CRON"
MARKER_END = "# END COMERCIAL_SAAS_CRON"


def _run(cmd: list[str], input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def ensure_cron_service_active() -> None:
    """Garante que o daemon do cron esteja ativo no sistema."""
    for svc in ("cron", "crond"):
        res = _run(["systemctl", "is-active", svc], check=False)
        if res.returncode == 0 and res.stdout.strip() == "active":
            print(f"  [OK] Serviço {svc} já está ativo.")
            return

    print("  [INFO] Tentando iniciar/habilitar serviço cron...")
    start_res = _run(["sudo", "systemctl", "enable", "--now", "cron"], check=False)
    if start_res.returncode == 0:
        print("  [OK] Serviço cron habilitado e iniciado.")
    else:
        print("  [AVISO] Não foi possível gerenciar o serviço cron via systemctl; verifique permissões de sudo.")


def build_cron_block() -> str:
    python_bin = shutil.which("python3") or "/usr/bin/python3"
    return "\n".join(
        [
            MARKER_BEGIN,
            "SHELL=/bin/bash",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            f"0 3 * * * {python_bin} {ORCHESTRATOR} backup >> {CRON_LOG} 2>&1",
            f"30 8 * * * {python_bin} {ORCHESTRATOR} dunning >> {CRON_LOG} 2>&1",
            MARKER_END,
        ]
    )


def install_user_crontab() -> None:
    if not ORCHESTRATOR.exists():
        raise SystemExit(f"[ERRO] Orquestrador não encontrado em: {ORCHESTRATOR}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    CRON_LOG.touch(exist_ok=True)

    current = _run(["crontab", "-l"], check=False)
    existing_lines = current.stdout.splitlines() if current.returncode == 0 else []

    # Remove bloco anterior caso exista, preservando outras entradas do usuário
    cleaned_lines: list[str] = []
    inside_block = False
    for line in existing_lines:
        if line.strip() == MARKER_BEGIN:
            inside_block = True
            continue
        if line.strip() == MARKER_END:
            inside_block = False
            continue
        if not inside_block:
            cleaned_lines.append(line)

    base_text = "\n".join(cleaned_lines).strip()
    new_block = build_cron_block()
    final_crontab = f"{base_text}\n\n{new_block}\n" if base_text else f"{new_block}\n"

    _run(["crontab", "-"], input_text=final_crontab, check=True)
    print(f"  [OK] Crontab atualizado com sucesso (log em {CRON_LOG}).")


def show_installed_crontab() -> None:
    print("\n=== Crontab Atual do Usuário ===")
    res = _run(["crontab", "-l"], check=False)
    if res.returncode == 0 and res.stdout.strip():
        print(res.stdout.strip())
    else:
        print("(vazio)")


def main() -> None:
    print("=== Passo 1: Verificando serviço cron no servidor tbtc ===")
    ensure_cron_service_active()

    print("\n=== Passo 2: Instalando rotinas agendadas do Comercial SaaS ===")
    install_user_crontab()

    show_installed_crontab()
    print("\n[CONCLUÍDO] Agendamento diário configurado:")
    print("  - 03:00 -> manage_comercial_saas.py backup")
    print("  - 08:30 -> manage_comercial_saas.py dunning")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERRO] Falha ao executar comando ({exc.cmd}): {exc.stderr}", file=sys.stderr)
        sys.exit(exc.returncode)