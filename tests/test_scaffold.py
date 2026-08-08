def test_package_imports():
    import warden
    import warden.agent

    assert warden is not None
    assert warden.agent is not None