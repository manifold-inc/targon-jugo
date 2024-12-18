GREEN  := "\\u001b[32m"
RESET  := "\\u001b[0m\\n"
CHECK  := "\\xE2\\x9C\\x94"

set shell := ["bash", "-uc"]

default:
  just -l

build opts="":
  docker compose build {{opts}}

prod image version='latest':
  export VERSION={{version}} && docker compose pull
  export VERSION={{version}} && docker rollout {{image}}
  @printf " {{GREEN}}{{CHECK}} Images Started {{CHECK}} {{RESET}}"

rollback image:
  export VERSION=$(docker image ls --filter before=manifoldlabs/targon-{{image}}:latest --filter reference=manifoldlabs/targon-hub-{{image}} --format "{{{{.Tag}}" | head -n 1) && docker rollout {{image}}
