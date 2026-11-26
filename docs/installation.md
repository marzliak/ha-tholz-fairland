# Instalação

> **Guia ilustrativo.** Os nomes de menus podem variar conforme a versão do Home Assistant. Para a configuração da conta, veja [Configuração](configuration.md).

## 1. Antes de começar

Confirme os itens abaixo:

- [ ] **Home Assistant 2025.2.4 ou mais recente.** Testes automatizados com nuvem simulada passaram em **2025.2.4** e **2026.10.1**; versões intermediárias não foram testadas individualmente.
- [ ] **Bomba de calor Fairland / TurboSilence** já vinculada à conta do aplicativo do fabricante, com leitura de estado funcionando no aplicativo.
- [ ] **E-mail ou usuário e senha** dessa conta.
- [ ] **Acesso à internet** a partir do Home Assistant. A integração usa os serviços do fabricante, hospedados na AWS.
- [ ] **Backup recente** do Home Assistant. A entrada de configuração da integração será gravada no armazenamento do Home Assistant (veja [Privacidade](privacy.md)).

Equipamentos de outras linhas Tholz não são suportados.

## 2. Método de instalação

**0.3.0 é a primeira versão pública (2026-11-16).** Veja a [release](https://github.com/marzliak/ha-tholz-fairland/releases/tag/v0.3.0). Recomendamos o HACS. Use o pacote manual se você não usa HACS.

### Pelo HACS

**O HACS já deve estar instalado.**

1. No HACS, procure por **Tholz Fairland** e abra a integração.
2. Selecione **Download** e confirme.
3. Reinicie o Home Assistant.
4. Vá em **Configurações → Dispositivos e serviços → Adicionar integração → Tholz Fairland** e siga [Configuração](configuration.md).

As seções 3 e 4 abaixo são a alternativa de instalação manual; quem instalou pelo HACS não precisa copiar arquivos.

## 3. Baixe e confira o pacote

1. Baixe o [pacote manual `tholz-fairland-0.3.0-integration.zip`](https://github.com/marzliak/ha-tholz-fairland/releases/download/v0.3.0/tholz-fairland-0.3.0-integration.zip).
2. Se a página de release publicar um valor SHA-256 para o arquivo, compare-o antes de extrair:

   ```bash
   sha256sum tholz-fairland-0.3.0-integration.zip
   ```

   No Windows (PowerShell): `Get-FileHash .\tholz-fairland-0.3.0-integration.zip -Algorithm SHA256`

3. Extraia o arquivo em uma pasta temporária do seu computador. A estrutura esperada é:

   ```text
   . (raiz do ZIP, sem pasta externa)
   ├── LICENSE
   └── custom_components/
       └── tholz_fairland/
           ├── __init__.py
           ├── manifest.json
           ├── config_flow.py
           ├── sensor.py
           ├── ...
   ```

## 4. Copie a pasta da integração

A única pasta que deve ir para o Home Assistant é `custom_components/tholz_fairland/`. O arquivo `LICENSE` não é copiado para dentro do Home Assistant.

O destino final é:

```text
<pasta de configuração do Home Assistant>/custom_components/tholz_fairland/
```

Onde fica a pasta de configuração:

| Instalação | Pasta de configuração |
|---|---|
| Home Assistant OS ou Supervisado | `/config` (acesse pelo add-on Samba, SSH ou File editor) |
| Container (Docker) | A pasta de configuração que você mapeou no container |
| Home Assistant Core (ambiente Python) | Normalmente `~/.homeassistant` |

Cuidados:

- Copie `tholz_fairland` diretamente dentro de `custom_components`. Não crie `custom_components/tholz_fairland/tholz_fairland`.
- Não deixe arquivos de backup antigos (`.bak`, cópias com data) dentro de `custom_components`. O Home Assistant tenta carregar as pastas dessa árvore.
- Se já existir uma versão anterior, substitua a pasta inteira por esta, mantendo uma cópia da versão antiga fora de `custom_components`. Veja [Atualização](updating.md).

## 5. Reinicie o Home Assistant

Vá em **Configurações → Sistema → Reiniciar** e confirme. Aguarde o Home Assistant voltar.

Reiniciar é necessário para que o código novo seja carregado. Recarregar apenas a entrada da integração não basta após copiar arquivos.

## 6. Confirme que a integração aparece

1. Vá em **Configurações → Dispositivos e serviços → Adicionar integração**.
2. Pesquise por **Tholz Fairland**.

Se a integração não aparecer:

- confira se a pasta está em `custom_components/tholz_fairland/` e se `manifest.json` está dentro dela;
- confira se o Home Assistant foi reiniciado após a cópia;
- consulte os registros em [Solução de problemas](troubleshooting.md#a-integração-não-aparece-na-lista).

Quando a integração aparecer, siga o [cadastro da conta](configuration.md).

## 7. Ícone da integração

A partir do Home Assistant **2026.3**, o pacote pode exibir a marca própria da integração. Versões anteriores mostram o ícone padrão de integração. Essa diferença é apenas visual e não afeta o funcionamento.

## 8. Remoção

1. Vá em **Configurações → Dispositivos e serviços → Tholz Fairland**.
2. Abra a entrada e escolha **Excluir** (ou **Remover**).
3. Apague a pasta `custom_components/tholz_fairland/` e reinicie o Home Assistant.

A remoção retira as entidades dessa entrada do Home Assistant. A retenção do histórico segue as regras do recorder do próprio Home Assistant. Se seu backup ou o armazenamento do Home Assistant estiver exposto, troque também a senha da conta do fabricante.

## Próximos passos

- [Configuração](configuration.md): cadastrar a conta e escolher o equipamento.
- [Operação](operation.md): entender entidades e limites.
- [Atualização](updating.md): como atualizar depois.
