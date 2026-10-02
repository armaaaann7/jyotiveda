.PHONY: install test lint fmt simulate serve schemas up down image

install:  ; uv venv && uv pip install -e ".[dev]"
test:     ; pytest
lint:     ; ruff check src tests ml && ruff format --check src tests ml
fmt:      ; ruff format src tests ml && ruff check --fix src tests ml
simulate: ; jyotiveda simulate
serve:    ; jyotiveda serve --port 8000
schemas:  ; jyotiveda schemas
up:       ; jyotiveda keygen && docker compose up -d
down:     ; docker compose down
image:    ; docker build -t jyotiveda:latest .
