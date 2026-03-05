"""
Tests for the prompt template system.
"""

import sys
import os

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.prompt.template import PromptTemplate, PromptTemplateManager


def test_prompt_template_creation():
    """Test prompt template creation and variable extraction."""
    template = PromptTemplate(
        name="test-template",
        template="Hello {name}, you are {age} years old.",
        description="A simple greeting template"
    )
    
    # Check that variables were extracted
    assert "name" in template.required_variables
    assert "age" in template.required_variables
    assert len(template.required_variables) == 2
    
    print("✓ Prompt template creation test passed")


def test_prompt_rendering():
    """Test prompt rendering with variables."""
    template = PromptTemplate(
        name="test-render",
        template="Hello {name}, you are {age} years old."
    )
    
    # Render with variables
    variables = {"name": "Alice", "age": 30}
    rendered = template.render(variables)
    
    assert rendered == "Hello Alice, you are 30 years old."
    
    print("✓ Prompt rendering test passed")


def test_prompt_validation():
    """Test prompt variable validation."""
    template = PromptTemplate(
        name="test-validation",
        template="Hello {name}, you are {age} years old."
    )
    
    # Valid variables
    valid_vars = {"name": "Alice", "age": 30}
    assert template.validate_variables(valid_vars) == True
    
    # Invalid variables (missing required)
    invalid_vars = {"name": "Alice"}
    assert template.validate_variables(invalid_vars) == False
    
    print("✓ Prompt validation test passed")


def test_template_manager():
    """Test prompt template manager functionality."""
    manager = PromptTemplateManager()
    
    # Create and register a template
    template = PromptTemplate(
        name="manager-test",
        template="Process {input} and generate {output}",
        version="1.0.0"
    )
    
    # Register template
    assert manager.register_template(template) == True
    
    # Retrieve template
    retrieved = manager.get_template("manager-test")
    assert retrieved is not None
    assert retrieved.name == "manager-test"
    
    # List templates
    template_list = manager.list_templates()
    assert len(template_list) == 1
    
    # Render prompt
    rendered = manager.render_prompt("manager-test", {"input": "data", "output": "result"})
    assert rendered == "Process data and generate result"
    
    print("✓ Template manager test passed")


def test_template_versioning():
    """Test template versioning functionality."""
    manager = PromptTemplateManager()
    
    # Create templates with different versions
    template_v1 = PromptTemplate(
        name="version-test",
        template="Version 1: {content}",
        version="1.0.0"
    )
    
    template_v2 = PromptTemplate(
        name="version-test",
        template="Version 2: {content}",
        version="2.0.0"
    )
    
    # Register both versions
    assert manager.register_template(template_v1) == True
    assert manager.register_template(template_v2) == True
    
    # Get latest version (should be v2)
    latest = manager.get_template("version-test")
    assert latest.version == "2.0.0"
    
    # Get specific version
    v1 = manager.get_template("version-test", "1.0.0")
    assert v1.version == "1.0.0"
    
    print("✓ Template versioning test passed")


def test_default_templates():
    """Test initialization of default templates."""
    manager = PromptTemplateManager()
    
    # Initialize default templates
    from ai.prompt.template import initialize_default_templates
    assert initialize_default_templates(manager) == True
    
    # Check that templates were added
    templates = manager.list_templates()
    assert len(templates) > 0
    
    # Try to render a default template
    rendered = manager.render_prompt("question_answering", {
        "question": "What is AI?",
        "context": "AI is artificial intelligence."
    })
    
    assert "What is AI?" in rendered
    assert "AI is artificial intelligence." in rendered
    
    print("✓ Default templates test passed")


if __name__ == "__main__":
    # Run all tests
    test_prompt_template_creation()
    test_prompt_rendering()
    test_prompt_validation()
    test_template_manager()
    test_template_versioning()
    test_default_templates()
    print("\n🎉 All prompt template tests passed!")
