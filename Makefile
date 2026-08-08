.PHONY: setup start stop help

help:
	@echo "make setup  — install Python + Node dependencies (first time)"
	@echo "make start  — run API + UI together"
	@echo "Then open http://localhost:5173"

setup:
	./setup

start:
	./dev
