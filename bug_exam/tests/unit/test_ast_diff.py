from bug_exam.validator.ast_diff import files_touched


def test_files_touched_single():
    patch = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1 +1 @@
-a
+b
"""
    assert files_touched(patch) == ["src/foo.py"]


def test_files_touched_multi():
    patch = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-a
+b
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1 +1 @@
-c
+d
"""
    assert files_touched(patch) == ["a.py", "b.py"]


def test_files_touched_empty():
    assert files_touched("") == []
