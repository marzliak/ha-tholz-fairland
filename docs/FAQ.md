# Perguntas frequentes

**A integração é oficial da Tholz ou da Fairland?**
Não. É um projeto independente e não afiliado. Depende dos serviços em nuvem do fabricante, que podem mudar sem aviso.

**Funciona sem internet ou sem a nuvem?**
Não. A integração é somente nuvem. Sem acesso à nuvem, não há leitura nem comando.

**Preciso de hardware local ou de um gateway?**
Não. Você precisa apenas do Home Assistant com acesso à internet e da conta já vinculada ao equipamento no aplicativo do fabricante.

**Quais equipamentos são suportados?**
Bombas de calor Fairland / TurboSilence vinculadas a uma conta. Outros produtos Tholz não são suportados.

**Posso controlar várias bombas de calor?**
Sim. Cada equipamento precisa de uma entrada própria. Repita o cadastro para cada um.

**Qual versão do Home Assistant é necessária?**
Testes automatizados com serviços de nuvem simulados passaram em **2025.2.4** e **2026.10.1**. A versão mínima declarada é 2025.2.4; isso não certifica todas as versões intermediárias nem todos os modelos.

**Posso instalar pelo HACS?**
Sim. No HACS, procure por **Tholz Fairland**, confirme o download, reinicie o Home Assistant e adicione a integração. Veja [Instalação](installation.md), incluindo a alternativa manual.

**A temperatura "atual" do aquecedor é a da piscina?**
Não. A temperatura atual é a leitura de **entrada** de água no equipamento. Não há sonda separada da piscina na integração. Veja [Operação](operation.md#significado-dos-sensores-de-temperatura).

**O que significa "Entrada / Piscina" no painel de exemplo?**
É a temperatura da água na entrada do equipamento, com o rótulo escolhido pelo autor do painel. Não é uma medição separada da piscina.

**O modo "Resfriar" aparece como "cold"?**
Sim. O valor interno é `cold`. O README do projeto usa o nome "Cool" para o mesmo modo. A interface do Home Assistant pode mostrar um rótulo diferente. Os valores aceitos estão em [Operação](operation.md#comandos).

**Por que uma temperatura antes aceita agora dá erro?**
No tipo 7, a faixa depende do modo: Automático **12–40 °C**, Aquecer **18–40 °C**, Resfriar **12–30 °C**. Desligado mantém **12–40 °C**, sem ligar ao definir apenas a temperatura. Limites reportados só podem restringir a faixa; o passo também é validado, sem arredondamento silencioso. Uma leitura recente confirma a política antes do envio. Modo desconhecido ou campos de limite/passo malformados recusam a temperatura. Veja [limites e passo](operation.md#limites-e-passo-da-temperatura).

**Posso alterar as agendas pela integração?**
Não. As agendas são somente leitura. Altere-as no aplicativo do fabricante.

**Como leio os dias de repetição das agendas?**
`repeat_days` lista os dias em português, de segunda a domingo, e `repeat_description` os apresenta em texto. `repeat_mask` conserva a máscara original: `0` é **Nenhum dia** e `127` é **Todos os dias**. Isso não altera os horários nem prevê a próxima execução. Veja [agendas](operation.md#agendas-somente-leitura).

**A integração mostra consumo de energia ou custo?**
Não. O painel mostra tempo ativo e percentual calculados pela integração a partir dos registros de atividade do fabricante, na janela móvel de 24 horas. Não são medição de energia, custo ou eficiência. Veja [métricas, cache e falhas](operation.md#métricas-das-últimas-24-horas).

**O sensor "Heat pump running" é um interruptor?**
Não. É um indicador do estado reportado de funcionamento. Não liga nem desliga o equipamento.

**Posso controlar a bomba de circulação?**
Não. A integração não expõe esse controle, e nenhum exemplo do projeto o comanda.

**A senha fica salva?**
Sim. Ela é guardada na entrada de configuração para permitir a recuperação automática. O campo é mascarado na tela, mas o valor não é criptografado individualmente. Veja [Privacidade](privacy.md).

**Preciso reautenticar com frequência?**
Não, se a conta não exigir verificação e a senha não tiver mudado. A integração tenta renovar a sessão automaticamente.

**O que faço se o código de verificação não for aceito?**
Solicite um novo código no aplicativo e tente de novo. Códigos expiram rápido.

**A conta pede uma etapa que o formulário não aceita. O que fazer?**
Conclua a etapa no aplicativo do fabricante e repita o cadastro. A integração não conclui todas as etapas possíveis da conta.

**Por que a integração não encontra meu equipamento?**
Confira se o equipamento está vinculado à conta usada no cadastro e se aparece no aplicativo. Veja [Solução de problemas](troubleshooting.md#não-aparece-nenhum-equipamento).

**Posso usar o exemplo de painel sem alterar nada?**
Só se o nome escolhido no cadastro for `Fairland`, que corresponde aos IDs do exemplo. Caso contrário, ajuste os IDs antes de salvar. Veja [Painel](dashboard.md).

**As automações de exemplo estão ativas?**
Não. Todas são opcionais e precisam ser copiadas e ativadas por você. Veja [Automações](automations.md).

**Por que os nomes das entidades estão em inglês e português?**
Os nomes são os criados pela integração nesta versão. Parte das entidades usa inglês e parte português. Os IDs derivam desses nomes.

**Como reporto um problema sem expor dados?**
Remova e-mail, senha, tokens e identificadores de qualquer trecho de registro antes de compartilhar. Veja [Solução de problemas](troubleshooting.md#como-coletar-registros-com-segurança).

**Onde está o diagnóstico para baixar?**
Nesta versão não há download de diagnóstico. Use os registros e os sensores de diagnóstico descritos em [Operação](operation.md).
