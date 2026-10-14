# Automações (opcionais)

Os exemplos deste guia estão em [`examples/automations.yaml`](../examples/automations.yaml) e [`examples/scripts.yaml`](../examples/scripts.yaml). **Nenhum exemplo está ativo por padrão.** Você decide quais copiar, quais alterar e quais ativar.

> Os exemplos apenas **notificam**, exceto um script manual que altera a temperatura-alvo e só roda quando você o executa. Nenhum exemplo controla a circulação da água, liga ou desliga o equipamento em horários, cria agendas ou toma decisões de consumo.

## Princípios

- **Opt-in explícito:** você copia o bloco, ajusta os IDs e cria a automação. Até lá, nada acontece.
- **Sem controle de bomba de circulação:** a integração não expõe bomba de circulação e nenhum exemplo a comanda.
- **Sem agendas:** as agendas do equipamento são somente leitura. Os exemplos não criam nem alteram horários.
- **Sem promessas de energia:** os valores de 24 horas são reportados pelo fabricante e não medem consumo.
- **Limites respeitados:** qualquer temperatura enviada deve estar entre **12 e 40 °C**.
- **Comandos reais:** toda ação de escrita afeta o equipamento físico.

## Como adicionar uma automação

1. Abra **Configurações → Automações e cenas → Criar automação → Criar nova automação**.
2. Escolha **Editar em YAML** e cole o bloco desejado.
3. Ajuste o `entity_id` para os seus IDs reais.
4. Salve. Só ative depois de testar.
5. Para testar uma ação sem esperar o gatilho, use **Executar ações** no menu da automação, quando sua versão do Home Assistant oferecer essa opção.

Para o script, copie o bloco de `scripts.yaml` para o arquivo `scripts.yaml` do seu Home Assistant, ou crie um script por **Configurações → Automações e cenas → Scripts → Criar script → Editar em YAML**.

## Exemplos disponíveis

### 1. Aviso de problema reportado (notificação)

Notifica quando o sensor **Problem** permanece ligado por 5 minutos.

- Gatilho: `binary_sensor.fairland_problem` muda para `on` por 5 minutos.
- Ação: cria uma notificação persistente no Home Assistant com o código de erro atual.
- Não tenta corrigir nada. Consulte o aplicativo do fabricante.

### 2. Equipamento offline (notificação)

Notifica quando o sensor **Online** permanece desligado por 15 minutos.

- Gatilho: `binary_sensor.fairland_online` muda para `off` por 15 minutos.
- Ação: notificação persistente.
- Um intervalo curto gera mais alarmes falsos durante falhas breves da nuvem. Escolha o tempo conforme seu caso.

### 3. Script manual: definir temperatura-alvo

Script que **só roda quando você o executa**. Ele recusa valores fora de 12 a 40 °C antes de enviar qualquer comando.

- Campo: `temperature` (número de 12 a 40).
- Ação: `water_heater.set_temperature` no aquecedor.
- Não há gatilho automático. Confirme o valor antes de executar.

## Notificações: escolha o canal

Os exemplos usam **notificação persistente**, que aparece na interface do Home Assistant e não depende de app ou serviço externo. Se quiser notificação no celular, troque o bloco `persistent_notification.create` por uma ação `notify.<seu_dispositivo>` do app do Home Assistant. Esse canal exige o app instalado e vinculado.

## Cuidados

- **Não copie automações de outros usuários sem revisar.** Confira cada `entity_id` e cada ação.
- **Não ative um script de temperatura em automação** sem uma condição clara. Repetir comandos em loop pode gerar tráfego à nuvem e mudanças físicas não esperadas.
- **Falhas de comando** podem deixar o equipamento indisponível mesmo com a restauração tentada. Verifique o equipamento fisicamente.
- Automações **não** podem contornar o limite de logins automáticos ([Configuração](configuration.md#recuperação-automática-da-sessão)).

## Próximos passos

- [Painel](dashboard.md): onde colocar os cartões de controle.
- [Solução de problemas](troubleshooting.md): quando um alerta dispara sem causa aparente.
