# Atualização

Este guia explica como atualizar a integração, o que acontece com entradas existentes e como reverter.

## Antes de atualizar

1. Faça um **backup** do Home Assistant. Ele contém as credenciais da integração ([Privacidade](privacy.md)).
2. Anote a versão atual da integração (`manifest.json` em `custom_components/tholz_fairland/`).
3. Confirme que a conta do aplicativo do fabricante funciona normalmente.

## Atualizar pelo pacote manual

1. Baixe o pacote da versão nova na [página da release](https://github.com/marzliak/ha-tholz-fairland/releases/tag/v0.3.0) (`tholz-fairland-0.3.0-integration.zip` para 0.3.0).
2. Se a versão antiga estiver em `custom_components/tholz_fairland/`, **mova a pasta antiga** para fora de `custom_components` (por exemplo, para `/config/.tholz-backup-<versão>/`).
3. Copie a pasta `tholz_fairland` do pacote novo para `custom_components/`.
4. Reinicie o Home Assistant ([Instalação](installation.md)).
5. Recarregue a entrada e confira o registro em **Configurações → Sistema → Registros** filtrando por `tholz_fairland`.

Não deixe duas pastas de versões diferentes dentro de `custom_components`. A pasta antiga guardada fora dali serve de cópia de segurança.

## Atualização pelo HACS

Se instalou pelo HACS, abra **Tholz Fairland** no HACS, selecione a atualização e confirme **Download**. Reinicie o Home Assistant e confira as entidades e os registros. Não é necessário remover nem adicionar novamente a entrada. Para a primeira instalação, veja [Instalação](installation.md).

## Entradas existentes são migradas

A versão 0.3.0 migra automaticamente entradas criadas por versões anteriores (esquemas 1.1 a 1.3) na primeira inicialização:

- **Identificadores, entidades, dispositivo e nomes são preservados.** Os IDs de entidade não mudam, então painéis e automações continuam válidos.
- A entrada passa ao esquema **1.4**. A recuperação automática passa a ser a única política.
- Se a entrada antiga já tinha usuário e senha no modo automático, eles são mantidos.
- Se a entrada antiga era do modo **interativo**, a senha **não** é adotada. A integração mantém o token de sessão e pede **reautenticação** quando precisar de uma senha nova. Nesse caso, siga o alerta **Reautenticar Tholz Fairland**.
- O segredo de cliente obsoleto (de versões antigas da nuvem) é removido e substituído por valor vazio, por compatibilidade.

Depois de atualizar, confira:

- o sensor **Online** responde;
- as entidades continuam com os mesmos IDs;
- não há alerta de reautenticação pendente, ou, se houver, você o resolveu.

## Reverter para uma versão anterior

A migração altera a entrada para o esquema **1.4**. Não presuma que uma versão anterior aceitará essa entrada: a compatibilidade de downgrade não foi validada. Para retornar com segurança, restaure o **backup anterior à atualização**, incluindo a pasta da integração e a configuração correspondente do Home Assistant. Trocar apenas o código é uma tentativa de recuperação, não uma reversão garantida.

1. Remova a pasta `tholz_fairland` nova de `custom_components/`.
2. Restaure a pasta antiga guardada.
3. Reinicie o Home Assistant.

Se a reversão falhar ou a entrada ficar em estado inconsistente, restaure o **backup** feito antes da atualização. Restaurar um backup substitui o estado atual do Home Assistant, então use-o somente se a reversão simples não bastar.

## Mudança de versão de Home Assistant

Atualize a integração e confirme que ela funciona na versão nova de Home Assistant antes de atualizar o Home Assistant em produção. Testes automatizados com nuvem simulada passaram em **2025.2.4** e **2026.10.1**. Teste a instalação da sua conta e equipamento após atualizar; versões intermediárias e mudanças futuras do fabricante não estão automaticamente certificadas.

## Alterar credenciais depois de atualizar

Se a senha mudou, use **Reconfigurar** (não remova a entrada). Veja [Configuração → Atualizar credenciais](configuration.md#atualizar-credenciais-reconfigurar).

## Próximos passos

- [Notas da versão 0.3.0](releases/0.3.0.md): o que mudou nesta versão.
- [Solução de problemas](troubleshooting.md): quando algo não funcionar após a atualização.
