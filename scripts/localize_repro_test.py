"""Unit tests for the localize helper (item 31.3 tailored tool).

Exercises the core: run a snippet that recurses into a REPO module, and confirm
the helper reproduces, keeps only in-repo frames, surfaces the recursion cycle,
and points `driver` at the domain function (not generic plumbing).
"""
import localize_repro as lr


def test_localizes_recursion_to_repo_frame(tmp_path, monkeypatch) -> None:
    root = tmp_path
    (root / "buggy.py").write_text(
        "def boom(n):\n"
        "    return boom(n + 1)\n"
    )
    snip = root / "snip.py"
    snip.write_text("import buggy\nbuggy.boom(0)\n")
    monkeypatch.chdir(root)
    monkeypatch.syspath_prepend(str(root))  # so `import buggy` resolves in-process

    out = lr._localize(str(snip), ctx=2, max_frames=8)

    assert out["ok"] is False
    assert out["kind"] == "exception"
    assert "RecursionError" in out["exc"]
    # only in-repo frames are reported (the <repro>/snippet frame is filtered out)
    files = {f["file"] for f in out["frames"]}
    assert files == {"buggy.py"}
    # the recursive frame repeats many times -> it is the cycle / driver
    assert out["driver"] is not None and "boom" in out["driver"]
    top = out["frames"][0]
    assert top["func"] == "boom" and top["repeat"] > 1
    # source window is attached and marks the failing line
    assert any("return boom(n + 1)" in s["t"] for s in top["src"])


def test_auto_imports_repo_package_for_importless_snippet(tmp_path, monkeypatch) -> None:
    # The weak model often forgets the import; the helper auto-imports the repo's
    # top-level package so an import-less snippet still reproduces.
    root = tmp_path
    pkg = root / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .core import boom\n")
    (pkg / "core.py").write_text("def boom(n):\n    return boom(n + 1)\n")
    snip = root / "s.py"
    snip.write_text("boom(0)\n")  # NO import — relies on auto-import of mypkg
    monkeypatch.chdir(root)
    monkeypatch.syspath_prepend(str(root))

    out = lr._localize(str(snip), ctx=2, max_frames=8)
    assert out["ok"] is False and out["kind"] == "exception"
    assert any(f["file"].endswith("core.py") for f in out["frames"])
    assert out["driver"] and "boom" in out["driver"]


def test_clean_snippet_reports_no_error(tmp_path, monkeypatch) -> None:
    snip = tmp_path / "ok.py"
    snip.write_text("x = 1 + 1\n")
    monkeypatch.chdir(tmp_path)
    out = lr._localize(str(snip), ctx=2, max_frames=8)
    assert out["ok"] is True
    assert out["kind"] == "no-error"


def test_snippet_syntax_error_is_reported(tmp_path, monkeypatch) -> None:
    snip = tmp_path / "bad.py"
    snip.write_text("def (:\n")
    monkeypatch.chdir(tmp_path)
    out = lr._localize(str(snip), ctx=2, max_frames=8)
    assert out["ok"] is False
    assert out["kind"] == "snippet-syntax-error"
