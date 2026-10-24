import httpx
import pytest

from devils_workshop.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, main

from tests.conftest import write
from tests.fakes import FakeOpenRouter, reply


def test_init_sample_then_plan_check(tmp_path, capsys):
    folder = tmp_path / "Saltmarsh"
    assert main(["init", str(folder), "--sample"]) == EXIT_OK
    assert (folder / "Story settings.md").is_file()
    assert main(["plan-check", str(folder)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Plan-check: The Saltmarsh Clock" in out and "Errors: 0" in out


def test_init_refuses_non_empty_folder(tmp_path, capsys):
    write(tmp_path / "taken", "x.txt", "x")
    assert main(["init", str(tmp_path / "taken")]) == EXIT_PROBLEMS
    assert "isn't empty" in capsys.readouterr().err


def test_init_title_and_sample_conflict(tmp_path):
    assert main(["init", str(tmp_path / "a"), "--sample", "--title", "X"]) == EXIT_USAGE


def test_plan_check_reports_errors_with_exit_code(tmp_path, capsys):
    folder = tmp_path / "s"
    main(["init", str(folder), "--sample"])
    write(folder, "characters/broken.md", "---\nname: [unclosed\n---\n")
    assert main(["plan-check", str(folder)]) == EXIT_PROBLEMS
    assert "ERROR   characters/broken.md:2" in capsys.readouterr().out


def test_plan_check_on_non_project(tmp_path, capsys):
    assert main(["plan-check", str(tmp_path)]) == EXIT_PROBLEMS
    assert "isn't a story project folder" in capsys.readouterr().err


def test_no_command_prints_help(capsys):
    assert main([]) == EXIT_USAGE
    assert "plan-check" in capsys.readouterr().out


def test_preview_summary_and_choices_by_label(tmp_path, capsys):
    folder = tmp_path / "s"
    main(["init", str(folder), "--sample"])
    capsys.readouterr()
    code = main(["preview", "1", "-p", str(folder), "--summary", "--leave-out", "style guide",
                 "--add", "Ada Quill", "--add-file", "notes/ideas.md"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "Preview: chapter 1 \"The Stopped Clock\" (whole-chapter mode)" in out
    assert "Style guide                  always included (unticked by you)" in out
    assert "Ada Quill                    added by you" in out
    assert "notes/ideas.md" in out
    assert "===== user =====" not in out  # --summary hides the full text


def test_preview_full_text_and_errors(tmp_path, capsys):
    folder = tmp_path / "s"
    main(["init", str(folder), "--sample"])
    capsys.readouterr()
    assert main(["preview", "2", "--scene", "1", "-p", str(folder)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "===== system =====" in out and "Later in this chapter (do not write yet):" in out
    assert main(["preview", "4", "--scene", "3", "-p", str(folder)]) == EXIT_PROBLEMS
    assert "hasn't been written yet" in capsys.readouterr().err
    assert main(["preview", "9", "-p", str(folder)]) == EXIT_PROBLEMS


# --- M3 commands (against the fake OpenRouter) ---------------------------------------------

@pytest.fixture
def fake_openrouter(monkeypatch, tmp_path):
    """Point the CLI at a fake OpenRouter and a temporary settings folder."""
    import devils_workshop.llm.openrouter as openrouter

    fake = FakeOpenRouter(endpoints={"z-ai/glm-5.3": [{"provider_name": "Z.ai"}]})
    real = openrouter.OpenRouterClient
    monkeypatch.setattr(openrouter, "OpenRouterClient",
                        lambda key, **kw: real(key, transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setenv("DW_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return fake


def test_models_command(fake_openrouter, capsys):
    assert main(["models", "--search", "glm"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "z-ai/glm-5.3" in out and "$1.40/$4.40 per M" in out and "settings: max_tokens, temperature" in out


def test_settings_command(fake_openrouter, capsys):
    assert main(["settings"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Writer's main model:      z-ai/glm-5.3" in out and "API key: not set" in out
    assert "Planner's main model:     z-ai/glm-5.3" in out


def test_live_test_command(fake_openrouter, capsys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key-0123456789abcdef")
    fake_openrouter.queue(reply("A lighthouse blinked once over the grey water, and the morning answered."))
    assert main(["live-test"]) == EXIT_OK
    assert "Result: OK." in capsys.readouterr().out


def test_cost_command(tmp_path, capsys):
    folder = tmp_path / "s"
    main(["init", str(folder), "--sample"])
    write(folder, "logs/costs.jsonl", '{"chapter": 1, "model": "z-ai/glm-5.3", "job": "write", "cost": 0.12}\n')
    capsys.readouterr()
    assert main(["cost", "-p", str(folder)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Costs: The Saltmarsh Clock" in out and "Total: $0.1200" in out
