from hippo.memory import bullets, frontmatter


def test_bullets():
    assert bullets("# T\n- one\n* two\ntext\n") == ["one", "two"]


def test_frontmatter():
    text = "---\nauthor: me\ncommits: [a1, 'b2']\nfeatures: []\n---\n## Changed\n"
    assert frontmatter(text) == {"author": "me", "commits": ["a1", "b2"], "features": []}


def test_frontmatter_missing_or_unclosed():
    assert frontmatter("## Changed\n") == {}
    assert frontmatter("---\nauthor: me\n") == {}
