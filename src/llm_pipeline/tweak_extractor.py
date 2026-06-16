"""
Step 1: Tweak Extraction & Parsing

This module extracts structured modifications from review text using LLM processing.
It converts natural language descriptions of recipe changes into structured
ModificationObject instances.
"""

import json
import os
from typing import List, Optional

from loguru import logger
from openai import OpenAI
from pydantic import ValidationError

from .models import ModificationObject, Recipe, Review
from .prompts import build_simple_prompt


class TweakExtractor:
    """Extracts structured modifications from review text using LLM processing."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-3.5-turbo"):
        """
        Initialize the TweakExtractor.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: OpenAI model to use for extraction
        """
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.model = model
        logger.info(f"Initialized TweakExtractor with model: {model}")

    def extract_modification(
        self,
        review: Review,
        recipe: Recipe,
        max_retries: int = 2,
    ) -> List[ModificationObject]:
        """
        Extract structured modifications from a review.

        A single review can describe multiple distinct modifications.
        Returns a list (possibly empty) of ModificationObjects.

        Args:
            review: Review object containing modification text
            recipe: Original recipe being modified
            max_retries: Number of retry attempts if parsing fails

        Returns:
            List of ModificationObjects (empty if review has no flag,
            extraction fails, or LLM returns no modifications)
        """
        if not review.has_modification:
            logger.warning("Review has no modification flag set")
            return []

        prompt = build_simple_prompt(
            review.text, recipe.title, recipe.ingredients, recipe.instructions
        )

        logger.debug(
            "Extracting modifications from review: {}...".format(review.text[:100])
        )

        raw_output = None
        data = None

        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=1500,
                )

                raw_output = response.choices[0].message.content
                logger.debug(f"LLM raw output: {raw_output}")

                if not raw_output:
                    logger.warning(f"Attempt {attempt + 1}: Empty response from LLM")
                    continue

                data = json.loads(raw_output)
                modifications_data = data.get("modifications", [])
                modifications = [ModificationObject(**m) for m in modifications_data]

                logger.info(
                    f"Successfully extracted {len(modifications)} modification(s) "
                    f"with {sum(len(m.edits) for m in modifications)} total edits"
                )
                return modifications

            except json.JSONDecodeError as e:
                logger.warning(f"Attempt {attempt + 1}: Failed to parse JSON: {e}")
                if attempt == max_retries:
                    logger.error(f"Max retries reached. Raw output: {raw_output}")

            except ValidationError as e:
                logger.warning(f"Attempt {attempt + 1}: Validation error: {e}")
                if attempt == max_retries:
                    logger.error(f"Max retries reached. Invalid data: {data}")

            except Exception as e:
                logger.error(f"Attempt {attempt + 1}: Unexpected error: {e}")
                if attempt == max_retries:
                    return []

        return []

    def extract_single_modification(
        self, reviews: list[Review], recipe: Recipe
    ) -> tuple[List[ModificationObject], Optional[Review]]:
        """
        Extract modifications from a single randomly selected review.

        Args:
            reviews: List of reviews to choose from
            recipe: Original recipe being modified

        Returns:
            Tuple of (modifications_list, source_Review).
            Returns ([], None) if no eligible reviews or extraction fails.
        """
        import random

        modification_reviews = [r for r in reviews if r.has_modification]

        if not modification_reviews:
            logger.warning("No reviews with modifications found")
            return [], None

        selected_review = random.choice(modification_reviews)
        logger.info(f"Selected review: {selected_review.text[:100]}...")

        modifications = self.extract_modification(selected_review, recipe)
        if modifications:
            logger.info(
                f"Successfully extracted {len(modifications)} modification(s) "
                f"from selected review"
            )
            return modifications, selected_review
        else:
            logger.warning("Failed to extract any modifications from selected review")
            return [], None

    def test_extraction(
        self, review_text: str, recipe_data: dict
    ) -> List[ModificationObject]:
        """Test extraction with raw text and recipe data."""
        review = Review(text=review_text, has_modification=True)
        recipe = Recipe(
            recipe_id=recipe_data.get("recipe_id", "test"),
            title=recipe_data.get("title", "Test Recipe"),
            ingredients=recipe_data.get("ingredients", []),
            instructions=recipe_data.get("instructions", []),
        )

        return self.extract_modification(review, recipe)
