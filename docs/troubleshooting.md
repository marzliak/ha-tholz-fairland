# Solução de problemas

Procure o sintoma abaixo. Cada item indica a causa provável e o que fazer. Se nada resolver, veja [Como coletar registros com segurança](#como-coletar-registros-com-segurança) antes de abrir um relato.

## Sintomas e ações

### A integração não aparece na lista

- Confirme que a pasta é `custom_components/tholz_fairland/` e que `manifest.json` está dentro dela ([Instalação](installation.md)).
- Confirme que o Home Assistant foi **reiniciado** após a cópia. Recarregar a entrada não carrega código novo.
- Veja **Configurações → Sistema → Registros** e filtre por `tholz_fairland`. Erros de importação ou de versão aparecem ali.
- Confira a versão do Home Assistant: os testes automatizados passaram em 2025.2.4 e 2026.10.1. Versões futuras ou outras combinações podem exigir verificação própria.

### Não aparece nenhum equipamento

Mensagem: **"Nenhuma bomba de calor Fairland compatível foi encontrada nesta conta."**

- Confirme, no aplicativo do fabricante, que o equipamento está vinculado **a esta conta** (não a outra conta da mesma família).
- A integração só lista equipamentos Fairland / TurboSilence. Outros produtos Tholz são ignorados por design.
- Confirme que o aplicativo mostra o equipamento como disponível.

### Credenciais rejeitadas

Mensagem: **"O e-mail ou a senha da conta foi rejeitado."**

- Confira o e-mail ou usuário (é o mesmo do aplicativo) e a senha. Atenção a maiúsculas e a espaços.
- Se você trocou a senha recentemente, use a senha nova.
- Tente entrar no aplicativo do fabricante com as mesmas credenciais. Se falhar lá, o problema está na conta.

### Pede verificação ou nova senha

- Digite o código recebido no momento, sem espaços. Códigos expiram rápido; peça outro se o aviso disser que o valor não foi aceito.
- Se a tela pedir nova senha, a senha digitada passa a ser a guardada.
- Se a mensagem disser **"Finalize o login no app do fabricante"**, a conta exige uma etapa que a integração não conclui. Conclua-a no aplicativo e repita o cadastro.

### Pede reautenticação

Surge quando a sessão não pôde ser renovada automaticamente.

1. Abra o alerta **Reautenticar Tholz Fairland** em **Configurações → Dispositivos e serviços**.
2. Informe a conta e a senha atuais. Se a conta exigir código, siga a verificação.
3. Se a mensagem disser **"não corresponde a este dispositivo"**, você entrou com outra conta. Use a conta original. Nada é alterado nesse caso.

Se o alerta voltar com frequência, verifique se a senha foi trocada no aplicativo. Nesse caso, use **Reconfigurar** com a senha nova ([Configuração](configuration.md#atualizar-credenciais-reconfigurar)).

### Recuperação em espera ou logins espaçados

A integração limita os logins automáticos por entrada: no máximo 3 logins com senha por dia e intervalo mínimo de 15 minutos entre eles. Falhas temporárias do serviço também geram espera de 30 segundos. Isso é esperado após várias falhas seguidas. Aguarde; não repita o cadastro em sequência, porque cada login pode enviar um código à conta.

### Entidades ausentes ou indisponíveis

- **Entidades ausentes:** os sensores de temperatura, indicadores e agendas só são criados se o equipamento reportar o campo no momento do cadastro ou da recarga. Se faltar uma entidade, aguarde um ciclo de atualização e depois **recarregue a entrada** (**Dispositivos e serviços → Tholz Fairland → ⋮ → Recarregar**).
- **Indisponíveis:** geralmente a nuvem ou a conexão do equipamento caiu. Veja o sensor **Online**. Se estiver desligado, confira o aplicativo do fabricante. Se o aplicativo também mostrar offline, o problema está no equipamento ou na conexão dele com a nuvem.
- Se as entidades continuarem indisponíveis por mais de alguns minutos com o aplicativo funcionando, colete os registros ([abaixo](#como-coletar-registros-com-segurança)).
- Os IDs de entidade podem ter sufixo numérico (`_2`) se já existir um nome igual. Confira os IDs reais em **Ferramentas do desenvolvedor → Estados** antes de editar painéis ou automações.

### Comando não aplicado, ou o valor voltou ao anterior

- Espere alguns segundos. Sucesso exige que o equipamento reporte o novo valor sem mudança pendente, o que pode levar um pouco.
- Se o valor voltou ao anterior, o equipamento não convergiu para o valor pedido e a integração restaurou o estado anterior. Tente novamente depois de confirmar que o equipamento está online.
- Use somente os valores permitidos: temperatura de 12 a 40 °C, modos Desligado/Automático/Aquecer/Resfriar e ventilador Silent/Intelligent.

### Falha ao restaurar o estado anterior

A integração trata este caso como falha no retorno ao estado anterior (erro interno `ROLLBACK_FAILED`). Isso significa que o equipamento pode estar em um estado diferente do esperado. Nesta candidata, o texto exato exibido na interface não é garantido; registre a hora do ocorrido e o valor que você tentou definir.

- **Verifique o equipamento fisicamente** e pelo aplicativo do fabricante antes de qualquer outro comando.
- Ajuste o modo, a temperatura e o ventilador pelo aplicativo ou pelo Home Assistant somente depois de confirmar o estado real.

### Problem ligado ou código de erro diferente de zero

- O sensor **Problem** liga quando o código de erro é diferente de 0. A integração **não traduz** o código.
- Anote o valor em **Error code** e consulte o manual ou o aplicativo do fabricante.
- Não há ação automática: o equipamento pode precisar de atendimento técnico.
- Se o código voltar a 0 e **Problem** desligar, o alerta está resolvido.

### Métricas (24 h) ou agenda vazias

- As métricas vêm do serviço do fabricante e são consultadas no máximo a cada 5 minutos. Após uma falha, os sensores podem ficar sem valor até a próxima consulta bem-sucedida.
- As agendas são lidas junto com o estado. Se a leitura da agenda falhar, as entidades de agenda podem não ser criadas no cadastro. Recarregue a entrada.
- Nenhuma dessas métricas é de consumo de energia.

### Muitas consultas ou painel atrasado

- Intervalos menores geram mais consultas à nuvem. Ajuste em **Configurar** (faixa 10 a 300 s). Veja [Configuração → Intervalo de atualização](configuration.md#intervalo-de-atualização).

### Após trocar a senha da conta

- Use **Reconfigurar** com a senha nova. Não remova e recadastre: a remoção apaga entidades e personalizações ligadas à entrada.

## Como coletar registros com segurança

1. Atualize o Home Assistant e a integração.
2. Recarregue a entrada.
3. Veja **Configurações → Sistema → Registros** filtrando por `tholz_fairland`.
4. Para mais detalhe, aumente o nível de registro **somente** para a integração. Em `configuration.yaml`:

   ```yaml
   logger:
     default: warning
     logs:
       custom_components.tholz_fairland: debug
   ```

   Reinicie o Home Assistant, reproduza o problema, colete o registro e volte o nível para `warning`.

5. **Não** ative `debug` para `botocore`, `boto3`, `urllib3` ou para o logger raiz. A biblioteca AWS registra conteúdo de requisições e respostas nesse nível, incluindo tokens e dados de sessão. Veja [Privacidade](privacy.md).
6. Antes de compartilhar um trecho de registro, remova e-mail, senha, tokens, identificadores de conta e identificadores de equipamento.

Não publique senhas, tokens, identificadores de conta ou de equipamento, backups ou entradas completas de configuração em issues. Relatos de vulnerabilidade seguem a política em `SECURITY.md`, não uma issue pública.

## Relato de problema

Inclua: versão da integração, versão do Home Assistant, entidade afetada, comportamento esperado e observado, e o trecho de registro já sem dados pessoais. Use o rastreador de issues do repositório para erros reproduzíveis.
