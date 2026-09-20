"""Ejecuta la publicación real del workflow contra un remoto Git local."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ("tv_principales.m3u8", "estado.md", "estado.json")


class PublicationTests(unittest.TestCase):
    def test_first_publication_unchanged_and_update(self):
        bash = shutil.which("bash")
        if not bash and os.name == "nt":
            git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
            if git_bash.is_file():
                bash = str(git_bash)
        self.assertIsNotNone(bash, "La prueba necesita Bash (Git Bash en Windows).")

        workflow = (ROOT / ".github/workflows/update-playlist.yml").read_text(encoding="utf-8")
        publication = workflow.split("      - name: Guardar cambios\n", 1)[1]
        script = textwrap.dedent(publication.split("        run: |\n", 1)[1])

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            remote = directory / "remote.git"
            checkout = directory / "checkout"
            checkout.mkdir()

            def git(*args, cwd=checkout):
                return subprocess.run(
                    ["git", *args], cwd=cwd, check=True,
                    capture_output=True, text=True, encoding="utf-8",
                ).stdout.strip()

            git("init", "--bare", str(remote))
            git("init", "-b", "main")
            git("config", "user.name", "Publication test")
            git("config", "user.email", "publication@example.invalid")
            git("config", "commit.gpgsign", "false")
            (checkout / "README.md").write_text("Test repository\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-m", "Initial commit")
            git("remote", "add", "origin", str(remote))
            git("push", "-u", "origin", "main")

            def publish():
                result = subprocess.run(
                    [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                    cwd=checkout, capture_output=True, text=True, encoding="utf-8",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            initial = git("rev-parse", "HEAD")
            for name in OUTPUTS:
                (checkout / name).write_text("First publication\n", encoding="utf-8")
            (checkout / "unrelated.txt").write_text("Do not publish\n", encoding="utf-8")
            publish()
            first = git("rev-parse", "HEAD")
            self.assertNotEqual(initial, first)
            for name in OUTPUTS:
                self.assertEqual(git("show", f"main:{name}", cwd=remote), "First publication")
            self.assertNotIn("unrelated.txt", git("ls-tree", "--name-only", "main", cwd=remote))

            publish()
            self.assertEqual(git("rev-parse", "HEAD"), first)

            (checkout / OUTPUTS[0]).write_text("Updated playlist\n", encoding="utf-8")
            publish()
            self.assertNotEqual(git("rev-parse", "HEAD"), first)
            self.assertEqual(git("show", f"main:{OUTPUTS[0]}", cwd=remote), "Updated playlist")


if __name__ == "__main__":
    unittest.main()
