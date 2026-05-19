# Contributing to cc-anywhere

Thank you for your interest in contributing! This guide will help you understand the project structure and development workflow.

## Repository Structure

### Two Code Locations

This repository maintains code in two locations:

#### 1. Production Code (`src/cc_anywhere/`)

This is what gets installed when users run `pip install cc-anywhere`.

```
src/cc_anywhere/
├── __init__.py       # Package version and metadata
├── cli.py            # Entry points for command-line tools
├── main.py           # Core dashboard and UI logic
├── search.py         # Search functionality
├── sync.py           # Sync operations
├── core.py           # Core utilities
└── path_utils.py     # Cross-platform path handling
```

**Entry points** (defined in `pyproject.toml`):
```toml
[project.scripts]
cc-anywhere = "cc_anywhere.cli:main"
cc-search = "cc_anywhere.cli:search_main"
claude-sync = "cc_anywhere.cli:sync_main"
```

#### 2. Development Scripts (Root Directory)

These are standalone scripts for development and testing:

```
├── claude_projects.py   # Dashboard development version
├── export_session.py    # Export functionality testing
├── import_session.py    # Import functionality testing
├── sync_history.py      # Sync development version
└── path_utils.py        # Shared utilities (duplicated)
```

**Purpose:**
- Quick iteration: `python claude_projects.py`
- Direct testing without reinstalling the package
- Debugging and experimentation
- **Not included in pip packages**

### Why This Structure?

1. **Development Speed**: Root scripts can be run directly during development
2. **Clean Distribution**: Only production code in `src/` gets packaged
3. **Separation of Concerns**: Development experiments don't affect production code
4. **Common Pattern**: Many Python projects use this structure (see requests, black, etc.)

## Development Workflow

### Setting Up Development Environment

```bash
# Clone the repository
git clone https://github.com/abecouse/cc-anywhere.git
cd cc-anywhere

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Verify installation
cc-anywhere --help
```

### Making Changes

#### For Production Code Changes:
1. Edit files in `src/cc_anywhere/`
2. Test using the installed commands: `cc-anywhere`, `cc-search`, etc.
3. Run tests: `pytest`
4. If you changed shared code (like `path_utils.py`), update the root version too

#### For Development Script Changes:
1. Edit root scripts directly
2. Test by running them: `python claude_projects.py`
3. Consider whether changes should also go in production code

### Code Synchronization

**Critical:** `path_utils.py` exists in both locations:
- `/path_utils.py` - Used by development scripts
- `/src/cc_anywhere/path_utils.py` - Used by production package

**When you modify path_utils.py:**
1. Decide which is the "source of truth"
2. Copy changes to the other location
3. Run the sync test to verify: `pytest tests/test_code_sync.py`

**Best Practice:** Consider making development scripts import from production:
```python
# In root scripts, add at the top:
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
from cc_anywhere.path_utils import *
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_code_sync.py

# Run with coverage
pytest --cov=cc_anywhere --cov-report=html
```

### Test Structure

```
tests/
├── test_code_sync.py      # Ensures dev scripts match production
├── test_path_utils.py     # Cross-platform path handling
├── test_search.py         # Search functionality
└── test_sync.py           # Sync operations
```

### Writing Tests

Add tests for:
- New features
- Bug fixes
- Cross-platform compatibility
- Edge cases

Example:
```python
def test_new_feature():
    """Test that new feature works correctly."""
    result = my_new_function(input_data)
    assert result == expected_output
```

## Code Style

### Python Style Guide
- Follow PEP 8
- Use type hints where possible
- Write docstrings for public functions
- Keep functions focused and small

### Formatting
```bash
# Format code with black
black src/ tests/

# Check types with mypy
mypy src/cc_anywhere
```

## Cross-Platform Considerations

cc-anywhere supports macOS, Linux, and Windows.

**When writing code:**
1. Use `pathlib.Path` instead of string paths
2. Test clipboard operations on multiple platforms
3. Handle platform-specific behaviors in `path_utils.py`
4. Avoid hardcoded paths (use `Path.home()`, etc.)

**Platform-specific code:**
```python
import platform

if platform.system() == "Windows":
    # Windows-specific code
elif platform.system() == "Darwin":
    # macOS-specific code
else:
    # Linux/other
```

## Pull Request Process

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/my-new-feature`
3. **Make your changes**
   - Write code
   - Add/update tests
   - Update documentation if needed
4. **Run tests**: `pytest`
5. **Format code**: `black src/ tests/`
6. **Commit with clear messages**:
   ```
   Add search filtering by date range

   - Add --from and --to flags to search command
   - Update search.py to filter results by timestamp
   - Add tests for date range filtering
   ```
7. **Push to your fork**: `git push origin feature/my-new-feature`
8. **Open a Pull Request** with:
   - Clear description of changes
   - Why the change is needed
   - How it was tested
   - Any breaking changes

## Common Tasks

### Adding a New Command

1. Add function in `src/cc_anywhere/main.py`:
```python
def my_new_command():
    """Do something useful."""
    # Implementation
```

2. Add entry point in `src/cc_anywhere/cli.py`:
```python
def my_command_main():
    """Entry point for my-command."""
    # Argument parsing
    my_new_command()
```

3. Register in `pyproject.toml`:
```toml
[project.scripts]
my-command = "cc_anywhere.cli:my_command_main"
```

4. Test:
```bash
pip install -e .
my-command --help
```

### Adding a New Dependency

1. Add to `pyproject.toml`:
```toml
dependencies = [
    "rich>=10.0.0",
    "new-package>=1.0.0",
]
```

2. Reinstall:
```bash
pip install -e ".[dev]"
```

### Debugging

**Development scripts:**
```bash
python -m pdb claude_projects.py
```

**Installed package:**
```bash
python -m pdb -m cc_anywhere.cli
```

**Add debug logging:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.debug("Debug message")
```

## Release Process

(For maintainers)

1. Update version in `src/cc_anywhere/__init__.py`
2. Update version in `pyproject.toml`
3. Update CHANGELOG.md
4. Commit: `git commit -m "Release v0.2.0"`
5. Tag: `git tag v0.2.0`
6. Push: `git push && git push --tags`
7. Build: `python -m build`
8. Publish: `python -m twine upload dist/*`

## Getting Help

- **Issues**: Open an issue on GitHub
- **Questions**: Start a discussion on GitHub Discussions
- **Security**: Email security issues privately

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow
- Celebrate diverse perspectives

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.

---

**Thank you for contributing to cc-anywhere!**
