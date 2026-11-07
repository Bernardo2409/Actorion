# Gitord-Actions

Notifica um canal do Discord com os títulos dos commits (e autor) a cada push, usando GitHub Actions + webhook do Discord. Sem servidor, sem bot.

## Passos (para fazer no futuro)

### Opção A: workflow próprio (este projeto)
1. Discord: canal -> Editar canal -> Integrações -> Webhooks -> Novo webhook -> Copiar URL.
2. Copiar a pasta `.github/` deste projeto para o repositório que queres vigiar.
3. GitHub: repositório -> Settings -> Secrets and variables -> Actions -> New repository secret
   - Nome: `DISCORD_WEBHOOK`
   - Valor: o URL do webhook
4. Se o branch não for `main`, alterar `branches: [main]` em `.github/workflows/notify.yml`.
5. Fazer push. Ver o resultado no separador Actions.

### Opção B: integração nativa do Discord (sem código)
1. Criar outro webhook no Discord (de preferência noutro canal) e copiar o URL.
2. Acrescentar `/github` ao fim do URL.
3. GitHub: repositório -> Settings -> Webhooks -> Add webhook
   - Payload URL: o URL com `/github`
   - Content type: `application/json`
   - Events: Just the push event

Nota: usar canais diferentes para A e B, senão cada commit aparece duplicado.
