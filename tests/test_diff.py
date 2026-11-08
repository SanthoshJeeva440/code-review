import unittest

from reviewer.diff import chunk_files, detect_language, filter_files, is_excluded, parse_unified_diff, DEFAULT_EXCLUDES

SAMPLE = """diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@
 import os
-x = 1
+x = 2
+y = x / 0
 def f():
     return x
@@ -10,2 +11,3 @@ def g():
     a = 1
+    b = 2
     return a
diff --git a/web/new.ts b/web/new.ts
new file mode 100644
--- /dev/null
+++ b/web/new.ts
@@ -0,0 +1,2 @@
+export const a: number = 1;
+--- not a header
diff --git a/old.go b/old.go
deleted file mode 100644
--- a/old.go
+++ /dev/null
@@ -1 +0,0 @@
-package main
diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-{}
+{"a":1}
"""


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.files = {f.path: f for f in parse_unified_diff(SAMPLE)}

    def test_files_and_status(self):
        self.assertEqual(self.files["src/app.py"].status, "modified")
        self.assertEqual(self.files["web/new.ts"].status, "added")
        self.assertEqual(self.files["old.go"].status, "deleted")
        self.assertEqual(self.files["logo.png"].status, "binary")

    def test_line_numbers(self):
        app = self.files["src/app.py"]
        self.assertEqual(app.added_lines, {2, 3, 12})
        self.assertEqual(app.commentable_lines, {1, 2, 3, 4, 5, 11, 12, 13})
        self.assertEqual(len(app.hunks), 2)

    def test_added_line_that_looks_like_header(self):
        self.assertEqual(self.files["web/new.ts"].added_lines, {1, 2})

    def test_annotated_patch(self):
        ann = self.files["src/app.py"].annotated_patch().splitlines()
        self.assertIn("    3 +y = x / 0", ann)
        self.assertIn("      -x = 1", ann)
        self.assertIn("   12 +    b = 2", ann)

    def test_filter(self):
        kept, skipped = filter_files(list(self.files.values()), [], DEFAULT_EXCLUDES)
        self.assertEqual([f.path for f in kept], ["src/app.py", "web/new.ts"])
        self.assertEqual(set(skipped), {"old.go", "logo.png", "package-lock.json"})
        kept, _ = filter_files(list(self.files.values()), ["*.ts"], [])
        self.assertEqual([f.path for f in kept], ["web/new.ts"])

    def test_excludes(self):
        self.assertTrue(is_excluded("frontend/dist/app.js", ["dist/*"]))
        self.assertTrue(is_excluded("docs/a/b.md", ["docs/**"]))
        self.assertFalse(is_excluded("src/distance.py", ["dist/*"]))

    def test_language(self):
        self.assertEqual(detect_language("a/b/Main.kt"), "Kotlin")
        self.assertEqual(detect_language("Dockerfile"), "Dockerfile")
        self.assertEqual(detect_language("x.unknown"), "Text")

    def test_chunking(self):
        files = list(self.files.values())[:2]
        self.assertEqual(len(chunk_files(files, 10)), 2)
        self.assertEqual(len(chunk_files(files, 100000)), 1)


if __name__ == "__main__":
    unittest.main()
