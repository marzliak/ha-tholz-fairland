# Privacidade e segurança

Este guia explica quais dados a integração guarda, para quem os envia e o que você precisa proteger.

## O que fica armazenado

A integração guarda os itens abaixo **na entrada de configuração** do Home Assistant:

| Item | Por que é guardado |
|---|---|
| E-mail ou usuário da conta | Login de renovação e recuperação automática |
| Senha da conta | Recuperação automática sem sua intervenção |
| Tokens de atualização de sessão | Renovar a sessão sem pedir a senha a cada vez |
| Identificador do usuário na nuvem | Confirmar que uma reautenticação é da mesma conta |
| Identificador do equipamento | Vincular a entrada ao equipamento |
| Nome de exibição e intervalo de atualização | Preferências |

**Por que a senha é guardada:** sem ela, qualquer expiração de sessão exigiria que você entrasse de novo manualmente. A integração tenta a renovação sem senha primeiro; a senha é usada somente quando as outras formas falham.

## Armazenamento e backups

- O campo de senha é **mascarado na interface**, mas o valor **não é criptografado individualmente** no armazenamento do Home Assistant. Ele fica em `.storage`, no mesmo arquivo de entradas de configuração.
- **Backups que incluam a integração contêm as mesmas credenciais.** Restaurar um backup restaura a sessão e a senha.
- Trate `.storage/`, backups e cópias da pasta de configuração como **dados sensíveis**.
  - Mantenha backups criptografados, se a sua versão do Home Assistant oferecer essa opção.
  - Restrinja o acesso ao sistema de arquivos e ao SSH do Home Assistant.
  - Não envie backups, arquivos de `.storage` ou entradas de configuração a terceiros, nem a suporte.

A integração **não adiciona uma camada de criptografia privada** aos dados da entrada. Essa camada exigiria uma chave de descriptografia acessível pelo próprio runtime, o que não melhoraria o modelo de confiança normal do Home Assistant.

## Quando agir

Se o Home Assistant ou um backup estiver exposto:

1. Remova a integração ou troque a senha da conta no aplicativo do fabricante.
2. Invalide as sessões antigas, se o fabricante permitir.
3. Recadastre a entrada com a senha nova.

## Serviços externos

A integração envia credenciais e dados do equipamento **somente** aos serviços do fabricante necessários ao funcionamento:

- **Autenticação de conta** (Amazon Cognito, região `us-east-1`).
- **API do fabricante** (AWS API Gateway, região `us-east-1`), para descoberta de equipamentos e o autorizador de conexão.
- **Registros de atividade** do fabricante (mesma API), usados pela integração para calcular localmente o resumo móvel de 24 horas de tempo ativo e percentual. Veja [Operação](operation.md#métricas-das-últimas-24-horas).
- **Mensageria** do equipamento (AWS IoT Core, região `us-east-1`), para leitura do estado e envio de comandos.

Não há envio de análises, telemetria ou estatísticas aos mantenedores deste projeto.

Os serviços são operados pelo fabricante. A integração não controla a política de retenção deles.

## Registros (logs)

- A integração foi projetada para **não gravar** senhas, tokens, valores de SRP, `SECRET_HASH`, cabeçalhos de autorização ou sessões de desafio nas mensagens de registro e de erro.
- As sessões de desafio (código ou nova senha) existem apenas na memória do fluxo de configuração e são descartadas quando o fluxo termina.
- **Não ative `debug` para `botocore`, `boto3`, `urllib3` ou para o logger raiz.** A biblioteca AWS registra conteúdo completo de requisições e respostas nesse nível, incluindo tokens, senhas novas e sessões de desafio. Se precisar depurar, limite o nível a `custom_components.tholz_fairland`, como em [Solução de problemas](troubleshooting.md#como-coletar-registros-com-segurança).

Ao compartilhar um registro, remova e-mail, senha, tokens e identificadores antes.

## O que a integração não faz

- Não guarda o consumo de energia nem envia dados de uso a terceiros.
- Não lê nem altera outros dispositivos da conta.
- Não envia comando durante o cadastro, a reautenticação ou a validação de credenciais.
- Não oferece exportação de diagnóstico para download nesta versão.

## Recomendações

- Use uma senha exclusiva para a conta do equipamento.
- Ative a verificação em duas etapas da conta, quando o fabricante oferecer.
- Limite quem tem acesso administrativo ao Home Assistant.
- Ao vender ou descartar o Home Assistant, remova a integração antes e troque a senha da conta.

## Relatos de vulnerabilidade

Não abra issue pública para uma suspeita de vulnerabilidade. Siga a política em `SECURITY.md` (relato privado pelo GitHub). Não inclua credenciais, tokens, identificadores ou backups no relato.
