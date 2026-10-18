from matchmind.cli import main


def test_summary_command_prints_match_score_xg_and_top_moments(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["matchmind", "summary", "--seed", "7"])

    main()

    output = capsys.readouterr().out
    assert "Final score: Northbridge Rovers 1-1 Harbor City FC" in output
    assert "Northbridge Rovers: 2.36" in output
    assert "Harbor City FC: 1.12" in output
    assert "23:33 goal — Harbor City FC — Jonah Novak" in output
    assert "08:12 big_chance — Northbridge Rovers — Bruno Nakamura" in output
    assert output.count("\n  ") == 7
