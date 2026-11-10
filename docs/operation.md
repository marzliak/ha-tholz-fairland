# Operação

> **Diagrama ilustrativo** da arquitetura e dos ciclos abaixo. Não é captura de tela.

![Diagrama ilustrativo da arquitetura: Home Assistant, integração, nuvem do fabricante e equipamento](images/architecture.png)

## Como a integração funciona

- A integração é **somente nuvem**. O Home Assistant conversa com os serviços do fabricante, que se comunicam com o equipamento.
- O estado é lido periodicamente (intervalo configurável). Cada entidade lê o último valor guardado na memória, então o Home Assistant não faz uma consulta à nuvem por entidade.
- Os comandos seguem um ciclo controlado, descrito em [Ciclo de um comando](#ciclo-de-um-comando).

## Validade das leituras

- As entidades usam a última leitura válida, sem fazer uma consulta de rede por sensor.
- Se a leitura falhar ou o estado recebido for inválido, as entidades afetadas ficam **indisponíveis**, em vez de apresentar o cache como leitura atual.
- Mesmo sem uma falha explícita, uma leitura expira após **3 × o intervalo de atualização + 5 segundos**, contados com relógio monotônico. Uma nova leitura válida recupera a disponibilidade sem recriar entidades. Esse prazo mede a idade da consulta bem-sucedida; não certifica a idade física da medição do fabricante.
- Temperaturas vazias, em formato incorreto ou não finitas não são publicadas como números válidos. **Zero continua sendo uma leitura válida.**
- A confirmação de um comando não renova, sozinha, a validade da leitura dos sensores. As métricas e agendas continuam sujeitas aos seus próprios caches e timestamps.
- Após a confirmação, apenas os controles alterados mantêm os valores confirmados até a próxima leitura, evitando um retorno visual ao valor anterior. Isso não renova a idade das temperaturas; uma falha ou expiração continua tornando as entidades indisponíveis.

## Entidades criadas

Os nomes abaixo usam o prefixo que você escolheu no cadastro (aqui, `<nome>`). Os nomes são os que a integração cria nesta versão: as entidades do aquecedor, do ventilador, das temperaturas e dos indicadores usam **inglês**; as métricas de 24 horas e as agendas usam **português**. Os IDs de entidade derivam desses nomes e podem receber um sufixo numérico se já existir uma entidade com o mesmo nome. Os exemplos deste guia usam o nome padrão `Fairland`, por exemplo `water_heater.fairland_pool`.

| Entidade (nome) | Tipo | Acesso | Significado |
|---|---|---|---|
| `<nome> Pool` | Aquecedor (water heater) | Leitura e escrita | Modo de operação, temperatura-alvo e temperatura atual |
| `<nome> Fan mode` | Seletor | Leitura e escrita | Modo do ventilador: **Silent** ou **Intelligent** |
| `<nome> Ambient temperature` | Sensor de temperatura | Leitura | Temperatura ambiente reportada |
| `<nome> Inlet temperature` | Sensor de temperatura | Leitura | Temperatura da água na **entrada** do equipamento |
| `<nome> Outlet temperature` | Sensor de temperatura | Leitura | Temperatura da água na **saída** do equipamento |
| `<nome> Heat pump running` | Sensor binário (executando) | Leitura | Indicador reportado de que o equipamento está em funcionamento |
| `<nome> Online` | Sensor binário (conectividade) | Leitura | Conexão reportada pela nuvem |
| `<nome> Problem` | Sensor binário (problema) | Leitura | Ligado quando o código de erro é diferente de zero |
| `<nome> Error code` | Sensor de diagnóstico | Leitura | Código de erro bruto reportado pelo equipamento |
| `<nome> Tempo Aquecendo 24h` | Sensor de duração (segundos) | Leitura | Tempo ativo calculado pela integração a partir dos registros do fabricante, na janela móvel de 24 horas |
| `<nome> Ciclo de Aquecimento 24h` | Sensor (%) | Leitura | Percentual de atividade calculado pela integração para a mesma janela |
| `<nome> Agenda 1` a `Agenda 5` | Sensor | Leitura | Estado da agenda: `Ativa` ou `Desativada`, com horário |
| `<nome> Agendas Ativas` | Sensor (contagem) | Leitura | Quantas agendas estão ativas |
| `<nome> Agenda Versão` | Sensor de diagnóstico | Leitura | Versão da agenda reportada pelo equipamento |
| `<nome> Agenda Atualizada` | Sensor de diagnóstico (data/hora) | Leitura | Última atualização da agenda reportada |

Notas:

- Os sensores de temperatura só são criados se o equipamento reportar o valor **no momento do cadastro ou da recarga**. Se faltar uma entidade, veja [Entidades ausentes](troubleshooting.md#entidades-ausentes-ou-indisponíveis).
- As entidades de agenda também são criadas no cadastro, quando o equipamento informa a agenda.
- **Online** e **Problem** só existem se o equipamento reportar esses campos.
- A disponibilidade depende dos campos que o equipamento e a nuvem reportam.

![Diagrama ilustrativo do mapa de entidades: quais entidades a integração cria e como se agrupam](images/entities.png)

## Significado dos sensores de temperatura

| Sensor | O que representa |
|---|---|
| Temperatura ambiente | Temperatura do ar, segundo o equipamento |
| Temperatura de entrada | Água que **entra** no equipamento |
| Temperatura de saída | Água que **sai** do equipamento |

Importante:

- A **temperatura atual** do aquecedor (`<nome> Pool`) é a leitura de **entrada**, não a de saída nem a ambiente.
- Na captura do painel de exemplo, o cartão **Entrada / Piscina** mostra a temperatura da **água na entrada** do equipamento. **Não é uma sonda separada da piscina.** Se a água da piscina estiver muito diferente da leitura de entrada, isso é esperado: a leitura reflete o que passa pelo equipamento.
- A integração não calibra os sensores. Os valores são os que o fabricante reporta.
- Esta candidata não foi conferida contra a posição física das sondas. Confirme as leituras no equipamento ou no aplicativo do fabricante.

## Significado dos indicadores

| Indicador | Ligado quando | Limites |
|---|---|---|
| Heat pump running | O equipamento reporta a flag de funcionamento | Mostra o estado reportado. **Não é um interruptor de energia** e não mede a potência de aquecimento. |
| Online | A nuvem reporta o equipamento conectado | Se desligado, os comandos podem falhar. Veja [Solução de problemas](troubleshooting.md). |
| Problem | O código de erro é diferente de 0 | Não interpreta o código. Consulte o manual ou o aplicativo do fabricante para o significado. |
| Error code | Sempre mostra o número reportado | Valor bruto, sem tradução. |

## Métricas das últimas 24 horas

- **Origem dos dados:** a API do fabricante fornece as linhas da tabela `activeTime`. A integração seleciona os registros de `heat0` a partir do corte da janela móvel de **24 horas** e soma suas durações de atividade não negativas.
- **Cálculo local:** a função `summarize_metrics_document`, em `custom_components/tholz_fairland/cloud/client.py`, converte essa soma de milissegundos para segundos e calcula o percentual em relação à janela de 24 horas. **Tempo Aquecendo 24h** tem arredondamento a uma casa decimal; **Ciclo de Aquecimento 24h**, a duas. O percentual não é um valor pronto fornecido pelo serviço.
- **Cache:** o resumo fica em memória por **5 minutos**, independentemente do intervalo de atualização do estado. A próxima leitura após esse prazo tenta consultar as métricas novamente; as entidades não recalculam a janela a cada segundo.
- **Falhas que não sejam de autenticação:** se a primeira consulta falhar, o resumo é `{}` e os sensores ficam sem valor, não em zero. Se já houver um resumo, ele é preservado e pode ficar desatualizado. O resultado de fallback também respeita a espera de 5 minutos antes de outra consulta.
- **Autenticação/autorização:** respostas HTTP **401/403** são propagadas como erro de autenticação, não ocultadas como uma falha opcional de métricas.
- O resultado depende dos registros recebidos; não garante histórico completo nem equivalência a todos os gráficos do aplicativo. **Não mede energia, potência, kWh, custo ou eficiência.**


## Agendas: somente leitura

- As agendas (`Agenda 1` a `Agenda 5`) são **somente leitura**. Você as visualiza no Home Assistant, mas **não as edita** por ele.
- Para alterar uma agenda, use o aplicativo do fabricante.
- Estado: `Ativa · HH:MM-HH:MM` ou `Desativada · HH:MM-HH:MM`.
- Atributos de cada agenda: `slot`, `active`, `turn_on`, `turn_off`, `repeat_mask`, `repeat_days`, `repeat_description` e `read_only: true`.
- `repeat_mask` preserva o valor numérico de `rept`. `repeat_days` contém a lista dos dias selecionados, com nomes em português, ordenados de **segunda a domingo**. `repeat_description` apresenta esses dias em texto legível.
- Na máscara, os valores são: segunda **32**, terça **16**, quarta **8**, quinta **4**, sexta **2**, sábado **1** e domingo **64**. As combinações somam os valores dos dias selecionados.
- `0` resulta em `repeat_days: []` e `repeat_description: "Nenhum dia"`; `127` lista os sete dias e tem descrição **"Todos os dias"**. Zero não é apresentado como execução única.
- Os dias exibidos descrevem a repetição informada, não uma previsão da próxima execução.
- A integração não cria, altera nem liga/desliga agendas.

## Comandos

Você pode alterar três itens: **modo de operação**, **temperatura-alvo** e **modo do ventilador**. Nenhum outro campo é gravado.

| Comando | Valores aceitos | Observação |
|---|---|---|
| Modo de operação | Desligado (`off`), Automático (`automatic`), Aquecer (`heat`), Resfriar (`cold`) | Estes são os valores internos da integração. O README do projeto chama o último modo de "Cool"; veja [FAQ](FAQ.md). |
| Temperatura-alvo | Automático: 12–40 °C; Aquecer: 18–40 °C; Resfriar: 12–30 °C | Limites reportados podem restringir a faixa. Veja as regras abaixo, inclusive para Desligado. |
| Modo do ventilador | Silent, Intelligent | Dois valores somente. |

Para alterar, use o cartão do aquecedor no painel, a ação do seletor ou a ferramenta padrão do Home Assistant. **Cada comando altera o equipamento físico.** Confirme a entidade antes de usar.

### Limites e passo da temperatura

A entidade e o transporte usam a mesma política para o Fairland **tipo 7**:

| Modo | Faixa-base da temperatura-alvo |
|---|---|
| Automático (`automatic`) | 12–40 °C |
| Aquecer (`heat`) | 18–40 °C |
| Resfriar (`cold`, Cool) | 12–30 °C |
| Desligado (`off`) | 12–40 °C, preservando a faixa legada; definir apenas a temperatura não liga o equipamento |

- Limites válidos de `minSp` e `maxSp` só podem **restringir** a faixa-base, nunca ampliá-la. Se a faixa resultante for inconsistente, o pedido de temperatura é recusado.
- O passo vem de `stepSp`, com prioridade sobre `step`; ambos usam décimos de grau. **Somente se os dois estiverem ausentes**, mantém-se o passo legado de **1 °C**. Um campo de limite ou passo presente mas malformado não é tratado como ausente: o pedido de temperatura é recusado.
- O valor precisa ser um múltiplo exato de **0,1 °C** e pertencer à grade do passo, contada a partir do **mínimo efetivo**. Não há arredondamento silencioso nem ajuste automático para a faixa.
- Antes do envio, o transporte valida o pedido contra uma **leitura recente** do estado. Uma automação ou ação direta não contorna a política; uma mudança de modo ou de limites desde a leitura da interface pode fazer o pedido ser recusado.
- Um modo reportado desconhecido impede comandos de temperatura. Mudar **somente o modo** não acrescenta `sp` nem corrige a temperatura automaticamente.
- Antes do primeiro envio, os valores de restauração também são validados. Temperatura de base incompatível com o modo original ou passo, ou base malformada, bloqueia o comando; não é reenviada no rollback.

Automações que antes usavam apenas a faixa geral de 12–40 °C podem passar a receber erro. Confira o modo, a faixa e o passo atuais antes de definir a temperatura.

### Ciclo de um comando

![Diagrama ilustrativo do ciclo de um comando: leitura de base, envio, convergência e retorno à base em caso de falha](images/command-lifecycle.png)

1. A integração **lê o estado atual** do equipamento, valida o pedido e guarda os valores que serão alterados. Uma temperatura inválida é recusada antes do envio.
2. Envia **somente** o campo solicitado por aquela ação: temperatura não reenvia modo ou ventilador antigos; modo e ventilador também não reenviam outros controles.
3. Aguarda até **45 segundos** pelo estado reportado. Sucesso exige que o estado reportado coincida com o valor pedido **e** que não haja mudança pendente.
4. Se o equipamento não convergir, a integração tenta **restaurar o valor anterior**.
5. Se a restauração também falhar, a mensagem indica falha no retorno ao estado anterior. **Verifique o equipamento fisicamente.**

Consequências práticas:

- Se o valor pedido já for o estado reportado e não houver mudança pendente, a ação confirma essa condição sem reenviar o comando.
- O valor mostrado no Home Assistant pode demorar alguns segundos para mudar.
- Se o equipamento não aceitar o comando, o valor volta ao anterior.
- Um comando com falha pode deixar o equipamento indisponível por algum tempo, mesmo após a tentativa de restauração.

## Limites desta versão

- **Somente nuvem.** Sem nuvem, não há leitura nem comando. Não há modo local.
- **Sem controle de agendas.** As agendas são somente leitura.
- **Somente os três comandos listados.** Nenhum outro modo, horário ou função é suportado.
- **Sem métricas de energia.** Os tempos e percentuais são calculados localmente a partir dos registros do fabricante e não representam consumo.
- **Dependência do fabricante.** Mudanças no serviço, na conta ou nas interfaces podem deixar entidades indisponíveis.

## Próximos passos

- [Painel](dashboard.md): exemplo de cartão pronto para adaptar.
- [Automações](automations.md): alertas opcionais.
- [Solução de problemas](troubleshooting.md): quando algo não bate.
