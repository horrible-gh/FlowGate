def pytest_addoption(parser):
    parser.addoption("--voice-downloads", action="store_true", default=False, help="Run isolated real voice installs using pinned upstream downloads")
