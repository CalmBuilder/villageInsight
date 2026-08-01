from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_app_script_background_lifecycle_and_logs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    bin_dir = tmp_path / "bin"
    project.mkdir()
    bin_dir.mkdir()
    (project / "frontend" / "node_modules").mkdir(parents=True)
    (project / "docker").mkdir()
    (project / ".env").write_text("", encoding="utf-8")
    shutil.copy2(Path(__file__).parents[1] / "app.sh", project / "app.sh")

    _write_executable(bin_dir / "curl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(bin_dir / "node", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(bin_dir / "docker", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        bin_dir / "uv",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> uv.calls
if [[ " $* " == *" uvicorn "* || " $* " == *" village-insight-worker "* ]]; then
  trap 'exit 0' TERM INT
  while true; do sleep 0.1; done
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "npm",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> npm.calls
if [[ " $* " == *" run preview "* ]]; then
  trap 'exit 0' TERM INT
  while true; do sleep 0.1; done
fi
exit 0
""",
    )

    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PORT": "49137",
        "API_PORT": "49138",
        "START_TIMEOUT_SECONDS": "10",
        "STOP_TIMEOUT_SECONDS": "5",
    }

    started = subprocess.run(
        [project / "app.sh", "start"],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert started.returncode == 0, started.stderr
    assert "已启动" in started.stdout
    npm_calls = (project / "npm.calls").read_text(encoding="utf-8")
    assert "--prefix frontend run build" in npm_calls
    assert "--prefix frontend run preview" in npm_calls
    assert "run dev" not in npm_calls
    uv_calls = (project / "uv.calls").read_text(encoding="utf-8")
    assert "run alembic upgrade head" in uv_calls
    assert "run village-insight-bootstrap" in uv_calls
    assert (project / "data" / "run" / "app.pid").is_file()
    for component in (
        "api",
        "worker-parse",
        "worker-hermes",
        "worker-materialize",
        "frontend",
    ):
        assert (project / "data" / "run" / f"{component}.pid").is_file()
        assert (project / "logs" / f"{component}.log").is_file()

    status = subprocess.run(
        [project / "app.sh", "status"],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert status.returncode == 0
    assert "supervisor: 运行中" in status.stdout
    assert "frontend: 运行中" in status.stdout
    assert "API ready: 正常" in status.stdout

    stopped = subprocess.run(
        [project / "app.sh", "stop"],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert "PostgreSQL 保持运行" in stopped.stdout
    assert not (project / "data" / "run" / "app.pid").exists()
