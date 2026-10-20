.DEFAULT_GOAL := up

COMPOSE := docker compose -f docker-compose.yaml

.PHONY: up down

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down
