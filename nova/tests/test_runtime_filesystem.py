from asgiref.sync import async_to_sync

from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.terminal import TerminalCommandError

from .runtime_command_base import TerminalExecutorCommandTestCase


class FilesystemCommandTests(TerminalExecutorCommandTestCase):
    def test_touch_and_tee_create_and_append_root_files(self):
        executor = self._build_executor()

        created = async_to_sync(executor.execute)("touch note.txt")
        written = async_to_sync(executor.execute)('tee note.txt --text "hello"')
        appended = async_to_sync(executor.execute)('tee note.txt --text " world" --append')
        content = async_to_sync(executor.execute)("cat note.txt")

        self.assertIn("Created empty file /note.txt", created)
        self.assertIn("Wrote 5 bytes to /note.txt", written)
        self.assertIn("Wrote 6 bytes to /note.txt", appended)
        self.assertEqual(content, "hello world")

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("touch /skills/blocked.txt")

    def test_mkdir_supports_recursive_p_flag(self):
        memory_tool = self._create_memory_tool()
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=memory_tool)
        )

        created = async_to_sync(executor.execute)("mkdir -p /memory/preferences/editors")
        created_again = async_to_sync(executor.execute)("mkdir -p /memory/preferences/editors")

        self.assertIn("Ensured directory /memory/preferences/editors", created)
        self.assertIn("Ensured directory /memory/preferences/editors", created_again)
        self.assertIn("preferences/", async_to_sync(executor.execute)("ls /memory"))
        self.assertIn("editors/", async_to_sync(executor.execute)("ls /memory/preferences"))

    def test_mkdir_p_rejects_file_in_parent_chain(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('echo "hello" > /note.txt')

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mkdir -p /note.txt/archive")

    def test_cat_supports_line_numbering_with_file_and_stdin(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('tee /tmp/data.csv --text "alpha\\nbeta\\ngamma"')

        file_result = async_to_sync(executor.execute)("cat -n /tmp/data.csv | tail -1")
        stdin_result = async_to_sync(executor.execute)("cat -n < /tmp/data.csv | tail -1")

        self.assertEqual(file_result, "3\tgamma")
        self.assertEqual(stdin_result, "3\tgamma")

    def test_head_and_tail_support_numeric_short_form_with_file_and_stdin(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('tee /tmp/lines.txt --text "1\\n2\\n3\\n4\\n5\\n6"')

        head_file = async_to_sync(executor.execute)("head -5 /tmp/lines.txt")
        tail_file = async_to_sync(executor.execute)("tail -1 /tmp/lines.txt")
        head_stdin = async_to_sync(executor.execute)("cat /tmp/lines.txt | head -5")
        tail_stdin = async_to_sync(executor.execute)("cat /tmp/lines.txt | tail -1")

        self.assertEqual(head_file, "1\n2\n3\n4\n5")
        self.assertEqual(tail_file, "6")
        self.assertEqual(head_stdin, "1\n2\n3\n4\n5")
        self.assertEqual(tail_stdin, "6")

    def test_grep_supports_combined_short_flags_and_head_short_count(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)(
            'tee /openrouter_activity_2026-04-06.csv --text '
            '"kind\\ncache-hit\\nCACHE-hit\\ncache-store\\nCache-refresh\\ncache-read\\nCACHE-refresh\\nnetwork"'
        )

        piped = async_to_sync(executor.execute)(
            "grep -i cache /openrouter_activity_2026-04-06.csv | head -5"
        )
        combined = async_to_sync(executor.execute)(
            "grep -in cache /openrouter_activity_2026-04-06.csv"
        )

        self.assertEqual(
            piped,
            "\n".join(
                [
                    "/openrouter_activity_2026-04-06.csv:cache-hit",
                    "/openrouter_activity_2026-04-06.csv:CACHE-hit",
                    "/openrouter_activity_2026-04-06.csv:cache-store",
                    "/openrouter_activity_2026-04-06.csv:Cache-refresh",
                    "/openrouter_activity_2026-04-06.csv:cache-read",
                ]
            ),
        )
        self.assertEqual(
            combined,
            "\n".join(
                [
                    "/openrouter_activity_2026-04-06.csv:2:cache-hit",
                    "/openrouter_activity_2026-04-06.csv:3:CACHE-hit",
                    "/openrouter_activity_2026-04-06.csv:4:cache-store",
                    "/openrouter_activity_2026-04-06.csv:5:Cache-refresh",
                    "/openrouter_activity_2026-04-06.csv:6:cache-read",
                    "/openrouter_activity_2026-04-06.csv:7:CACHE-refresh",
                ]
            ),
        )

    def test_wc_supports_line_counts_for_files_and_pipelines(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)(
            'tee /openrouter_activity_2026-04-06.csv --text '
            '"kind\\ncache-hit\\nCACHE-hit\\ncache-store\\nCache-refresh\\ncache-read\\nCACHE-refresh\\nnetwork"'
        )

        file_result = async_to_sync(executor.execute)(
            "wc -l /openrouter_activity_2026-04-06.csv"
        )
        pipeline_result = async_to_sync(executor.execute)(
            "grep cache /openrouter_activity_2026-04-06.csv | wc -l"
        )

        self.assertEqual(file_result, "8 /openrouter_activity_2026-04-06.csv")
        self.assertEqual(pipeline_result, "3")

    def test_rm_supports_force_flag_and_multiple_paths(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("mkdir /out")
        async_to_sync(executor.execute)('echo "hello" > /out/index.html')

        removed = async_to_sync(executor.execute)("rm -f /out/index.html /out/missing.html")
        missing_only = async_to_sync(executor.execute)("rm -f /out/missing.html")

        self.assertEqual(removed, "Removed /out/index.html")
        self.assertEqual(missing_only, "")
        self.assertEqual(async_to_sync(executor.execute)("ls /out"), "")

    def test_rm_force_keeps_real_path_errors(self):
        executor = self._build_executor()

        with self.assertRaises(TerminalCommandError) as protected_error:
            async_to_sync(executor.execute)("rm -f /tmp")
        self.assertIn("Cannot remove protected path: /tmp", str(protected_error.exception))

        async_to_sync(executor.execute)("mkdir /out")
        async_to_sync(executor.execute)('echo "hello" > /out/index.html')
        with self.assertRaises(TerminalCommandError) as non_empty_error:
            async_to_sync(executor.execute)("rm -f /out")
        self.assertIn("Directory not empty: /out", str(non_empty_error.exception))

    def test_rm_supports_recursive_directory_deletion(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("mkdir /out")
        async_to_sync(executor.execute)("mkdir /out/nested")
        async_to_sync(executor.execute)('echo "hello" > /out/nested/index.html')

        removed = async_to_sync(executor.execute)("rm -rf /out")
        remaining = async_to_sync(executor.execute)('find / -name "*out*"')

        self.assertEqual(removed, "Removed /out")
        self.assertEqual(remaining, "")

    def test_find_supports_unix_like_name_and_type_filters(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("mkdir /gallery")
        async_to_sync(executor.execute)("mkdir /gallery/nested")
        async_to_sync(executor.execute)('printf "a" > /gallery/a.jpg')
        async_to_sync(executor.execute)('printf "b" > /gallery/b.png')
        async_to_sync(executor.execute)('printf "c" > /gallery/nested/c.jpeg')
        async_to_sync(executor.execute)('printf "note" > /gallery/nested/readme.txt')

        image_files = async_to_sync(executor.execute)(
            'find /gallery -type f -name "*.jpg" -o -name "*.png" -o -name "*.jpeg"'
        )
        directories = async_to_sync(executor.execute)("find /gallery -type d")

        self.assertIn("/gallery/a.jpg", image_files)
        self.assertIn("/gallery/b.png", image_files)
        self.assertIn("/gallery/nested/c.jpeg", image_files)
        self.assertNotIn("readme.txt", image_files)
        self.assertIn("/gallery", directories)
        self.assertIn("/gallery/nested", directories)
        self.assertNotIn(".jpg", directories)

    def test_find_supports_multiple_roots_and_lists_all_visible_paths(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("mkdir /gallery")
        async_to_sync(executor.execute)("mkdir /gallery/nested")
        async_to_sync(executor.execute)('printf "a" > /gallery/a.jpg')
        async_to_sync(executor.execute)('printf "tmp" > /tmp/alpha.txt')

        listed = async_to_sync(executor.execute)("find /gallery /tmp")
        jpg_only = async_to_sync(executor.execute)('find /gallery /tmp -type f -name "*.jpg"')

        self.assertIn("/gallery", listed)
        self.assertIn("/gallery/nested", listed)
        self.assertIn("/gallery/a.jpg", listed)
        self.assertIn("/tmp/alpha.txt", listed)
        self.assertEqual(jpg_only, "/gallery/a.jpg")

    def test_find_rejects_legacy_and_unsupported_expressions_cleanly(self):
        executor = self._build_executor()

        with self.assertRaises(TerminalCommandError) as legacy_shape:
            async_to_sync(executor.execute)("find / out")
        with self.assertRaises(TerminalCommandError) as iname_error:
            async_to_sync(executor.execute)('find / -iname "*.jpg"')
        with self.assertRaises(TerminalCommandError) as missing_name:
            async_to_sync(executor.execute)("find / -name")
        with self.assertRaises(TerminalCommandError) as unsupported_type:
            async_to_sync(executor.execute)("find / -type x")

        self.assertEqual(str(legacy_shape.exception), "Path not found: /out")
        self.assertIn("Unsupported find expression.", str(iname_error.exception))
        self.assertEqual(str(missing_name.exception), "Missing value for -name")
        self.assertEqual(str(unsupported_type.exception), "Unsupported find type: x")

    def test_terminal_reports_clean_usage_errors_for_unix_like_flags(self):
        executor = self._build_executor()
        async_to_sync(executor.execute)('tee /tmp/data.csv --text "alpha\\nbeta"')

        with self.assertRaises(TerminalCommandError) as wc_flag:
            async_to_sync(executor.execute)("wc -x /tmp/data.csv")
        with self.assertRaises(TerminalCommandError) as cat_flag:
            async_to_sync(executor.execute)("cat -x /tmp/data.csv")
        with self.assertRaises(TerminalCommandError) as head_flag:
            async_to_sync(executor.execute)("head -x /tmp/data.csv")
        with self.assertRaises(TerminalCommandError) as rm_usage:
            async_to_sync(executor.execute)("rm")

        self.assertEqual(str(wc_flag.exception), "Unsupported wc flag: -x")
        self.assertEqual(str(cat_flag.exception), "Unsupported cat flag: -x")
        self.assertEqual(str(head_flag.exception), "Unsupported head flag: -x")
        self.assertEqual(str(rm_usage.exception), "Usage: rm [-f] [-r|-R] <path> [<path> ...]")

    def test_terminal_supports_wc_printf_file_and_truthy_helpers(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('printf "alpha beta\\n" > /tmp/data.txt')
        async_to_sync(executor.execute)("mkdir /empty-dir")
        async_to_sync(executor.vfs.write_file)(
            "/tmp/blob.bin",
            b"\x00\x01",
            mime_type="application/octet-stream",
        )

        wc_default = async_to_sync(executor.execute)("wc /tmp/data.txt")
        wc_chars = async_to_sync(executor.execute)("wc -c /tmp/data.txt")
        wc_words = async_to_sync(executor.execute)('printf "one two three" | wc -w')
        file_output = async_to_sync(executor.execute)("file /tmp/data.txt /tmp/blob.bin /empty-dir")
        printf_output = async_to_sync(executor.execute)('printf "plain output"')

        self.assertEqual(wc_default, "1 2 11 /tmp/data.txt")
        self.assertEqual(wc_chars, "11 /tmp/data.txt")
        self.assertEqual(wc_words, "3")
        self.assertIn("/tmp/data.txt: text/plain, 11 bytes", file_output)
        self.assertIn("/tmp/blob.bin: application/octet-stream, 2 bytes", file_output)
        self.assertIn("/empty-dir: directory", file_output)
        self.assertEqual(printf_output, "plain output")

        true_result = async_to_sync(executor.execute_result)("unknowncmd || true")
        false_result = async_to_sync(executor.execute_result)("false && pwd")

        self.assertEqual(true_result.status, 0)
        self.assertEqual(true_result.failed_segment_indexes, [1])
        self.assertEqual(false_result.status, 1)
        self.assertEqual(false_result.skipped_segment_indexes, [2])

    def test_terminal_printf_supports_placeholders_and_escapes(self):
        executor = self._build_executor()

        placeholder_output = async_to_sync(executor.execute)('printf "%s %d %f" test 42 3.5')
        repeated_output = async_to_sync(executor.execute)('printf "[%s]" a b c')

        self.assertEqual(placeholder_output, "test 42 3.500000")
        self.assertEqual(repeated_output, "[a][b][c]")

        with self.assertRaises(TerminalCommandError) as invalid_placeholder:
            async_to_sync(executor.execute)('printf "%q" test')

        self.assertEqual(str(invalid_placeholder.exception), "Unsupported printf placeholder: %q")

    def test_head_and_tail_support_byte_counts(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('printf "abcdef" > /tmp/letters.txt')
        async_to_sync(executor.vfs.write_file)(
            "/tmp/raw.bin",
            b"\xff\xfe\xfd",
            mime_type="application/octet-stream",
        )

        self.assertEqual(async_to_sync(executor.execute)("head -c 3 /tmp/letters.txt"), "abc")
        self.assertEqual(async_to_sync(executor.execute)("tail -c 3 /tmp/letters.txt"), "def")
        self.assertEqual(async_to_sync(executor.execute)('printf "abcdef" | head -c 2'), "ab")

        with self.assertRaises(TerminalCommandError) as binary_error:
            async_to_sync(executor.execute)("head -c 2 /tmp/raw.bin")

        self.assertIn("Binary file cannot be displayed as text", str(binary_error.exception))

    def test_rmdir_only_removes_empty_directories(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("mkdir /empty-dir")
        async_to_sync(executor.execute)("mkdir /non-empty")
        async_to_sync(executor.execute)('printf "x" > /non-empty/item.txt')

        removed = async_to_sync(executor.execute)("rmdir /empty-dir")
        self.assertEqual(removed, "Removed /empty-dir")

        with self.assertRaises(TerminalCommandError) as non_empty_error:
            async_to_sync(executor.execute)("rmdir /non-empty")
        with self.assertRaises(TerminalCommandError) as file_error:
            async_to_sync(executor.execute)("rmdir /non-empty/item.txt")

        self.assertIn("Directory not empty: /non-empty", str(non_empty_error.exception))
        self.assertIn("Not a directory: /non-empty/item.txt", str(file_error.exception))

    def test_root_listing_shows_root_files_skills_and_tmp_without_legacy_mounts(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("touch /note.txt")
        listing = async_to_sync(executor.execute)("ls /")

        self.assertIn("skills/", listing)
        self.assertIn("tmp/", listing)
        self.assertIn("note.txt", listing)
        self.assertNotIn("workspace/", listing)
        self.assertNotIn("thread/", listing)

    def test_ls_supports_common_flags_and_aliases(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("mkdir /docs")
        async_to_sync(executor.execute)('echo "hello" > /note.txt')

        listing = async_to_sync(executor.execute)("ls -la /")
        file_listing = async_to_sync(executor.execute)("ls -l /note.txt")
        human_listing = async_to_sync(executor.execute)("ls -h /")
        human_file_listing = async_to_sync(executor.execute)("ls -lh /note.txt")
        alias_listing = async_to_sync(executor.execute)("la /")

        self.assertIn("drwxr-xr-x - - ./", listing)
        self.assertIn("drwxr-xr-x - - ../", listing)
        self.assertIn("-rw-r--r-- 6 text/plain note.txt", file_listing)
        self.assertIn("note.txt", human_listing)
        self.assertIn("-rw-r--r-- 6B text/plain note.txt", human_file_listing)
        self.assertIn("note.txt", alias_listing)

    def test_ls_supports_simple_wildcards_only_in_final_segment(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('printf "sun" > /solar-system.txt')
        async_to_sync(executor.execute)('printf "sys" > /system-notes.txt')
        async_to_sync(executor.execute)('printf "fr" > /solaire.txt')
        async_to_sync(executor.execute)("mkdir /tmp/images")
        async_to_sync(executor.execute)("touch /tmp/a.png")
        async_to_sync(executor.execute)("touch /tmp/b.png")

        root_matches = async_to_sync(executor.execute)("ls /solar* /syst* /solaire*")
        png_matches = async_to_sync(executor.execute)("ls -lh /tmp/*.png")

        self.assertIn("solar-system.txt", root_matches)
        self.assertIn("system-notes.txt", root_matches)
        self.assertIn("solaire.txt", root_matches)
        self.assertIn("a.png", png_matches)
        self.assertIn("b.png", png_matches)

        with self.assertRaises(TerminalCommandError) as no_match:
            async_to_sync(executor.execute)("ls /missing*")
        with self.assertRaises(TerminalCommandError) as intermediate_glob:
            async_to_sync(executor.execute)("ls /*/note.txt")

        self.assertEqual(str(no_match.exception), "No matches for pattern: /missing*")
        self.assertEqual(
            str(intermediate_glob.exception),
            "ls wildcard expansion is supported only in the final path segment.",
        )

    def test_ls_supports_recursive_directory_listing(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("mkdir /subagents")
        async_to_sync(executor.execute)("mkdir /subagents/demo")
        async_to_sync(executor.execute)('printf "child" > /subagents/demo/result.txt')

        listing = async_to_sync(executor.execute)("ls -laR /subagents")

        self.assertIn("/subagents:", listing)
        self.assertIn("/subagents/demo:", listing)
        self.assertIn("result.txt", listing)

    def test_sort_supports_stdin_and_file_inputs(self):
        executor = self._build_executor()

        piped = async_to_sync(executor.execute)('printf "b\\na\\nc\\n" | sort')
        async_to_sync(executor.execute)('tee /tmp/list.txt --text "beta\\nalpha\\ngamma\\n"')
        from_file = async_to_sync(executor.execute)("sort /tmp/list.txt")

        self.assertEqual(piped, "a\nb\nc\n")
        self.assertEqual(from_file, "alpha\nbeta\ngamma\n")

    def test_sort_rejects_binary_input_and_unsupported_flags(self):
        executor = self._build_executor()

        async_to_sync(executor.vfs.write_file)("/tmp/image.bin", b"\x00\xff", mime_type="application/octet-stream")

        with self.assertRaises(TerminalCommandError) as binary_error:
            async_to_sync(executor.execute)("sort /tmp/image.bin")
        with self.assertRaises(TerminalCommandError) as flag_error:
            async_to_sync(executor.execute)("sort -r /tmp/image.bin")

        self.assertIn("Binary file cannot be displayed as text", str(binary_error.exception))
        self.assertEqual(str(flag_error.exception), "Unsupported sort flag: -r")
