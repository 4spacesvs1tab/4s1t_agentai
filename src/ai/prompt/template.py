"""
Prompt template system for the 4S1T Agent AI framework.

This module provides a flexible prompt template system that supports variables,
validation, versioning, and optimization.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
import json
import re
from utils.logger import setup_logger
logger = setup_logger(__name__)


@dataclass
class PromptTemplate:
    """
    A template for generating prompts with variables and metadata.
    """
    
    name: str
    template: str
    description: str = ""
    version: str = "1.0.0"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    tags: List[str] = field(default_factory=list)
    required_variables: List[str] = field(default_factory=list)
    optional_variables: List[str] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Extract variables from template after initialization."""
        if not self.required_variables and not self.optional_variables:
            self._extract_variables()
    
    def _extract_variables(self):
        """Extract variables from the template string."""
        # Find all {variable} patterns
        variables = re.findall(r'\{([^}]+)\}', self.template)
        
        # For now, we'll treat all variables as required
        # In a more advanced system, we could support optional variables with default values
        self.required_variables = list(set(variables))
    
    def render(self, variables: Dict[str, Any]) -> str:
        """
        Render the template with provided variables.
        
        Args:
            variables: Dictionary of variable names and values
            
        Returns:
            str: Rendered prompt
            
        Raises:
            ValueError: If required variables are missing
        """
        # Check required variables
        missing_vars = set(self.required_variables) - set(variables.keys())
        if missing_vars:
            raise ValueError(f"Missing required variables: {missing_vars}")
        
        # Render template
        try:
            rendered = self.template.format(**variables)
            return rendered
        except KeyError as e:
            raise ValueError(f"Variable not provided for template: {e}")
        except Exception as e:
            raise ValueError(f"Error rendering template: {e}")
    
    def validate_variables(self, variables: Dict[str, Any]) -> bool:
        """
        Validate that variables meet template requirements.
        
        Args:
            variables: Dictionary of variable names and values
            
        Returns:
            bool: True if variables are valid, False otherwise
        """
        # Check required variables
        missing_vars = set(self.required_variables) - set(variables.keys())
        if missing_vars:
            logger.warning(f"Missing required variables: {missing_vars}")
            return False
        
        # Additional validation could be added here based on variable types
        # or constraints defined in metadata
        
        return True
    
    def get_variable_info(self) -> Dict[str, List[str]]:
        """
        Get information about template variables.
        
        Returns:
            Dict[str, List[str]]: Dictionary with required and optional variables
        """
        return {
            "required": self.required_variables.copy(),
            "optional": self.optional_variables.copy()
        }
    
    def add_example(self, variables: Dict[str, Any], expected_output: str):
        """
        Add an example of template usage.
        
        Args:
            variables: Variables used in the example
            expected_output: Expected rendered output
        """
        example = {
            "variables": variables,
            "rendered": self.render(variables),
            "expected_output": expected_output,
            "added_at": datetime.now().isoformat()
        }
        self.examples.append(example)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert template to dictionary representation.
        
        Returns:
            Dict[str, Any]: Dictionary representation of the template
        """
        return {
            "name": self.name,
            "template": self.template,
            "description": self.description,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "tags": self.tags,
            "required_variables": self.required_variables,
            "optional_variables": self.optional_variables,
            "examples": self.examples,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PromptTemplate':
        """
        Create a PromptTemplate from dictionary representation.
        
        Args:
            data: Dictionary representation of the template
            
        Returns:
            PromptTemplate: Created template instance
        """
        # Handle datetime fields
        data_copy = data.copy()
        if "created_at" in data_copy:
            data_copy["created_at"] = datetime.fromisoformat(data_copy["created_at"])
        if "updated_at" in data_copy:
            data_copy["updated_at"] = datetime.fromisoformat(data_copy["updated_at"])
        
        return cls(**data_copy)


class PromptTemplateManager:
    """
    Manager for prompt templates in the 4S1T Agent AI framework.
    
    Handles template registration, versioning, retrieval, and management.
    """
    
    def __init__(self):
        """Initialize the prompt template manager."""
        self.templates: Dict[str, Dict[str, PromptTemplate]] = {}
        self.logger = logger
    
    def register_template(self, template: PromptTemplate) -> bool:
        """
        Register a prompt template.
        
        Args:
            template: The template to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            template_name = template.name
            
            if template_name not in self.templates:
                self.templates[template_name] = {}
            
            # Store by version
            self.templates[template_name][template.version] = template
            self.logger.info(f"Registered template: {template_name} v{template.version}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register template {template.name}: {e}")
            return False
    
    def get_template(self, name: str, version: Optional[str] = None) -> Optional[PromptTemplate]:
        """
        Get a registered template by name and optionally version.
        
        Args:
            name: Name of the template
            version: Version of the template (latest if None)
            
        Returns:
            PromptTemplate: The template, or None if not found
        """
        if name not in self.templates:
            self.logger.warning(f"Template {name} not found")
            return None
        
        template_versions = self.templates[name]
        
        if version:
            return template_versions.get(version)
        else:
            # Return latest version (assuming semantic versioning)
            versions = list(template_versions.keys())
            if versions:
                latest_version = sorted(versions, reverse=True)[0]
                return template_versions[latest_version]
        
        return None
    
    def list_templates(self) -> List[Dict[str, Any]]:
        """
        List all registered templates with their versions.
        
        Returns:
            List[Dict[str, Any]]: List of template information
        """
        template_list = []
        for name, versions in self.templates.items():
            for version, template in versions.items():
                template_info = {
                    "name": name,
                    "version": version,
                    "description": template.description,
                    "tags": template.tags,
                    "created_at": template.created_at.isoformat(),
                    "updated_at": template.updated_at.isoformat()
                }
                template_list.append(template_info)
        return template_list
    
    def render_prompt(self, template_name: str, variables: Dict[str, Any], 
                     version: Optional[str] = None) -> str:
        """
        Render a prompt using a registered template.
        
        Args:
            template_name: Name of the template
            variables: Variables to render the template with
            version: Version of the template to use (latest if None)
            
        Returns:
            str: Rendered prompt
            
        Raises:
            ValueError: If template not found or variables invalid
        """
        template = self.get_template(template_name, version)
        if not template:
            raise ValueError(f"Template {template_name} not found")
        
        if not template.validate_variables(variables):
            raise ValueError(f"Invalid variables for template {template_name}")
        
        return template.render(variables)
    
    def update_template(self, template: PromptTemplate) -> bool:
        """
        Update an existing template.
        
        Args:
            template: The updated template
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            template_name = template.name
            
            if template_name not in self.templates:
                self.logger.warning(f"Template {template_name} not registered, registering as new")
                return self.register_template(template)
            
            # Update timestamp
            template.updated_at = datetime.now()
            
            # Store by version
            self.templates[template_name][template.version] = template
            self.logger.info(f"Updated template: {template_name} v{template.version}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to update template {template.name}: {e}")
            return False
    
    def remove_template(self, name: str, version: Optional[str] = None) -> bool:
        """
        Remove a template or specific version.
        
        Args:
            name: Name of the template
            version: Version to remove (all versions if None)
            
        Returns:
            bool: True if removal was successful, False otherwise
        """
        try:
            if name not in self.templates:
                self.logger.warning(f"Template {name} not found")
                return False
            
            if version:
                if version in self.templates[name]:
                    del self.templates[name][version]
                    self.logger.info(f"Removed template: {name} v{version}")
                    
                    # If no versions left, remove the template entry
                    if not self.templates[name]:
                        del self.templates[name]
                    return True
                else:
                    self.logger.warning(f"Template version {name} v{version} not found")
                    return False
            else:
                # Remove all versions
                del self.templates[name]
                self.logger.info(f"Removed all versions of template: {name}")
                return True
        except Exception as e:
            self.logger.error(f"Failed to remove template {name}: {e}")
            return False


# Default prompt templates for common use cases
DEFAULT_TEMPLATES = [
    PromptTemplate(
        name="question_answering",
        template="Answer the following question:\n\n{question}\n\nContext:\n{context}",
        description="Template for question answering with context",
        tags=["qa", "information_retrieval"],
        metadata={"task": "question_answering"}
    ),
    PromptTemplate(
        name="summarization",
        template="Summarize the following text:\n\n{text}\n\nProvide a concise summary.",
        description="Template for text summarization",
        tags=["summarization", "text_processing"],
        metadata={"task": "summarization"}
    ),
    PromptTemplate(
        name="creative_writing",
        template="Write a creative piece based on the following prompt:\n\n{prompt}\n\nStyle: {style}\nLength: {length}",
        description="Template for creative writing tasks",
        tags=["creative", "writing"],
        required_variables=["prompt", "style", "length"],
        metadata={"task": "creative_writing"}
    )
]


def initialize_default_templates(manager: PromptTemplateManager) -> bool:
    """
    Initialize the manager with default templates.
    
    Args:
        manager: The template manager to initialize
        
    Returns:
        bool: True if initialization was successful, False otherwise
    """
    try:
        for template in DEFAULT_TEMPLATES:
            manager.register_template(template)
        return True
    except Exception as e:
        logger.error(f"Failed to initialize default templates: {e}")
        return False
