#!/usr/bin/env python3
"""
riptide_example.py — Minimal example showing how to use Riptide for code review.

This demonstrates the core workflow:
1. Initialize vector store
2. Embed code chunks  
3. Index a file's content
4. Query for similar code

Note: This is a test file to verify Riptide's PR review pipeline.
"""
import os
import sys
from pathlib import Path

# Add riptide to path
sys.path.insert(0, str(Path(__file__).parent))

from riptide.store import init_store, upsert_chunks, search
from riptide.embed import chunk_text, embed_texts


def demo_workflow():
    """Demonstrate the basic Riptide workflow."""
    db_path = "/tmp/riptide_demo.db"
    
    # Initialize the vector store
    init_store(db_path)
    print(f"✓ Vector store initialized at {db_path}")
    
    # Sample Python code to index
    sample_code = '''
def calculate_factorial(n):
    """Calculate factorial of n."""
    if n < 0:
        raise ValueError("Negative input not allowed")
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result


def fibonacci(n):
    """Return nth Fibonacci number."""
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
'''
    
    # Chunk and embed
    chunks = chunk_text(sample_code, chunk_size=800)
    print(f"✓ Split into {len(chunks)} chunks")
    
    # Embed (will fail if Ollama not running, but that's OK for demo)
    try:
        vectors = embed_texts(chunks)
        print(f"✓ Embedded {len(vectors)} chunks")
        
        # Upsert to store
        upsert_chunks(db_path, "demo.py", list(zip(chunks, vectors)))
        print("✓ Stored in vector database")
        
        # Search
        query = "recursive algorithm for tree traversal"
        results = search(db_path, embed_texts([query])[0])
        print(f"✓ Found {len(results)} similar chunks")
        
    except Exception as e:
        print(f"Note: Embedding requires Ollama running: {e}")
    
    print("\nDemo complete. This change tests the Riptide PR review pipeline.")


if __name__ == "__main__":
    demo_workflow()