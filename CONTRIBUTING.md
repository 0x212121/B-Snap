# Contributing to B-Snap

First off, thank you for considering contributing to B-Snap! It's people like you that make B-Snap such a great tool.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Workflow](#development-workflow)
- [Style Guidelines](#style-guidelines)
- [Commit Messages](#commit-messages)
- [Pull Request Process](#pull-request-process)
- [Community](#community)

## Code of Conduct

This project and everyone participating in it is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

### Prerequisites

- Python 3.11 or higher
- PostgreSQL 14+ (or SQLite for development)
- Node.js 18+ (for frontend assets)
- FFmpeg
- Git

### Setting Up Development Environment

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/b-snap.git
   cd b-snap
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   make install-dev
   # or: pip install -r requirements.txt -r requirements-dev.txt
   ```

4. **Install pre-commit hooks**
   ```bash
   make install-pre-commit
   ```

5. **Setup environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your local configuration
   ```

6. **Initialize database**
   ```bash
   make db-migrate
   ```

7. **Build assets**
   ```bash
   npm install
   npm run build
   ```

8. **Run the development server**
   ```bash
   make dev
   ```

## How Can I Contribute?

### Reporting Bugs

Before creating a bug report, please check the [existing issues](https://github.com/0x212121/b-snap/issues) to see if the problem has already been reported.

When creating a bug report, please include:

- **Use a clear and descriptive title**
- **Describe the exact steps to reproduce the problem**
- **Provide specific examples to demonstrate the steps**
- **Describe the behavior you observed and what behavior you expected**
- **Include screenshots or GIFs** if applicable
- **Include your environment details**:
  - OS and version
  - Python version
  - B-Snap version
  - Browser (if UI-related)
  - Camera model (if camera-related)

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, please include:

- **Use a clear and descriptive title**
- **Provide a step-by-step description of the suggested enhancement**
- **Provide specific examples to demonstrate the enhancement**
- **Explain why this enhancement would be useful**

### Contributing Code

#### Finding Issues to Work On

- Look for issues labeled [`good first issue`](https://github.com/0x212121/b-snap/labels/good%20first%20issue) for beginner-friendly tasks
- Look for issues labeled [`help wanted`](https://github.com/0x212121/b-snap/labels/help%20wanted) for tasks we'd love community help with
- Comment on an issue to let others know you're working on it

#### Creating a New Feature

1. **Open an issue first** to discuss the feature
2. Wait for feedback from maintainers
3. Once approved, proceed with implementation

## Development Workflow

### Branch Naming

- `feature/description` - New features
- `bugfix/description` - Bug fixes
- `docs/description` - Documentation updates
- `refactor/description` - Code refactoring
- `test/description` - Test additions/improvements

Example: `feature/add-camera-groups`

### Making Changes

1. **Create a new branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Write clean, readable code
   - Follow the style guidelines (see below)
   - Add tests for new functionality
   - Update documentation as needed

3. **Test your changes**
   ```bash
   # Run tests
   make test

   # Run linting
   make lint

   # Format code
   make format

   # Run pre-commit hooks
   make pre-commit
   ```

4. **Commit your changes** (see [Commit Messages](#commit-messages))

5. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Create a Pull Request**

## Style Guidelines

### Python Code Style

We use the following tools to enforce code quality:

- **Black** - Code formatting (line length: 100)
- **Ruff** - Fast Python linting
- **MyPy** - Static type checking (strict mode)
- **Bandit** - Security linting

Run all checks with:
```bash
make lint
make format
```

### Python Style Rules

- Follow [PEP 8](https://pep8.org/) with the modifications in `pyproject.toml`
- Use type hints for all function signatures
- Write docstrings for all public modules, classes, and functions using Google style
- Maximum line length: 100 characters
- Use double quotes for strings

Example:
```python
def capture_snapshot(camera_id: int, timeout: int = 10) -> bytes:
    """Capture snapshot from camera.
    
    Args:
        camera_id: The ID of the camera to capture from.
        timeout: Maximum time to wait in seconds.
        
    Returns:
        Raw bytes of the captured image.
        
    Raises:
        CameraNotFoundError: If camera doesn't exist.
    """
    # Implementation
```

### Import Order

```python
# 1. Standard library
import os
from datetime import datetime

# 2. Third-party packages
from fastapi import FastAPI
from sqlalchemy import Column

# 3. Local application
from app.db.database import Base
from app.models.user import User
```

### Frontend (JavaScript/CSS)

- Use 2 spaces for indentation
- Follow existing patterns in the codebase
- Run `npm run build` before committing

### Database Migrations

When modifying models:

```bash
# Generate migration
make db-makemigrations message="add description"

# Review the generated migration file
# Apply migration
make db-migrate
```

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/) specification:

### Format

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

- `feat` - New feature
- `fix` - Bug fix
- `docs` - Documentation changes
- `style` - Code style changes (formatting, semicolons, etc.)
- `refactor` - Code refactoring
- `perf` - Performance improvements
- `test` - Test additions or corrections
- `chore` - Build process or auxiliary tool changes

### Examples

```
feat(camera): add support for Hikvision cameras

fix(snapshot): resolve timeout issue on slow networks

docs(api): update authentication documentation

refactor(models): simplify camera status tracking

test(services): add unit tests for email notifier
```

## Pull Request Process

1. **Update documentation** - Update README.md or other docs if your changes affect usage

2. **Add tests** - All new functionality should have corresponding tests

3. **Ensure CI passes** - All checks must pass before review

4. **Fill out the PR template** - Include:
   - Description of changes
   - Related issue numbers
   - Testing performed
   - Screenshots (for UI changes)

5. **Request review** - Request review from maintainers

6. **Address feedback** - Make requested changes

7. **Squash and merge** - Once approved, maintainers will squash and merge

### PR Checklist

Before submitting a PR, ensure:

- [ ] Code follows style guidelines (`make lint` passes)
- [ ] Code is formatted (`make format`)
- [ ] Tests pass (`make test`)
- [ ] New tests added for new functionality
- [ ] Documentation updated
- [ ] Commit messages follow conventional commits
- [ ] PR description is clear and complete

## Testing

### Running Tests

```bash
# Run all tests
make test

# Run specific test file
pytest app/tests/unit/test_models/test_camera.py -v

# Run with coverage
make test-coverage

# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration
```

### Writing Tests

- Place tests in `app/tests/`
- Name test files `test_*.py`
- Name test functions `test_*`
- Use fixtures from `conftest.py`
- Mock external services (cameras, email, etc.)

Example:
```python
import pytest
from app.models.camera import Camera

@pytest.mark.unit
class TestCamera:
    def test_camera_creation(self, db_session):
        camera = Camera(name="Test Cam", ip_address="192.168.1.100")
        db_session.add(camera)
        db_session.commit()
        
        assert camera.id is not None
        assert camera.name == "Test Cam"
```

## Documentation

- Update `README.md` if adding major features
- Add docstrings to all public APIs
- Update `CHANGELOG.md` with your changes
- Update `docs/` if applicable

## Community

### Communication Channels

- **GitHub Issues** - Bug reports and feature requests
- **GitHub Discussions** - General questions and discussions

### Recognition

Contributors will be:
- Listed in the README.md (with permission)
- Mentioned in release notes
- Added to the contributors graph

## Questions?

If you have questions about contributing:

1. Check existing documentation
2. Search closed issues
3. Open a new discussion

Thank you for contributing to B-Snap! 🎉
