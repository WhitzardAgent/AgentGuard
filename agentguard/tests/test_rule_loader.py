from agentguard.policy.rules.loaders import load_rules


def test_load_rules_reads_utf8_files(tmp_path):
    rule_file = tmp_path / "utf8.rules"
    rule_file.write_text(
        "# 中文注释\n"
        "RULE: allow_ls\n"
        "ON: tool_call(shell.exec)\n"
        'CONDITION: args.cmd == "ls"\n'
        "POLICY: ALLOW\n",
        encoding="utf-8",
    )

    rules = load_rules(rule_file)

    assert len(rules) == 1
    assert rules[0].rule_id == "allow_ls"
