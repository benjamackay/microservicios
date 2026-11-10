.DEFAULT_GOAL := up

COMPOSE := docker compose -f docker-compose.yaml

.PHONY: up down

up:
	git submodule update --init --recursive
	@if [ -n "$$LLM_API_KEY" ] || ( [ -f .env ] && grep -Eq '^LLM_API_KEY=[^[:space:]]+' .env ); then \
		$(COMPOSE) --profile summaries up -d --build; \
	else \
		$(COMPOSE) up -d --build; \
		printf '%s\n' 'Summaries omitido: configura LLM_API_KEY en .env para activarlo.'; \
	fi

down:
	$(COMPOSE) down
