# Documentação — Tholz Fairland 0.3.0

Guias de uso em português (PT-BR). Versões em inglês estão em [`en/`](en/README.md).

> **Integração não oficial.** O Tholz Fairland não é afiliado nem endossado pela Tholz ou pela Fairland. Ela depende dos serviços em nuvem do fabricante e pode deixar de funcionar se esses serviços ou suas interfaces mudarem.

## Status desta versão

- **Primeira versão pública: [0.3.0](https://github.com/marzliak/ha-tholz-fairland/releases/tag/v0.3.0), de 2026-11-16.**
- **Instalação disponível:** pacote manual (`tholz-fairland-0.3.0-integration.zip`), descrito em [Instalação](installation.md).
- **HACS:** instalação por repositório personalizado, descrita em [Instalação](installation.md). A inclusão no catálogo padrão ainda não foi aprovada.
- **Compatibilidade de Home Assistant:** testes automatizados com rede bloqueada passaram em **2025.2.4** e **2026.10.1**, incluindo configuração, carregamento das entidades, recarga e reautenticação com serviços simulados. Isso não certifica todas as versões intermediárias nem substitui a verificação da sua conta/equipamento.

## Roteiro de leitura

| Guia | Para que serve | Versão em inglês |
|---|---|---|
| [Instalação](installation.md) | Pré-requisitos, cópia do pacote manual, verificação inicial | [en/installation.md](en/installation.md) |
| [Configuração](configuration.md) | Cadastro da conta, descoberta do equipamento, verificação, recuperação e reautenticação | [en/configuration.md](en/configuration.md) |
| [Operação](operation.md) | Entidades, significado dos sensores, comandos, limites e modos | [en/operation.md](en/operation.md) |
| [Painel](dashboard.md) | Exemplo de painel nativo e como adaptá-lo | [en/dashboard.md](en/dashboard.md) |
| [Automações](automations.md) | Alertas opcionais com ativação explícita pelo usuário | [en/automations.md](en/automations.md) |
| [Solução de problemas](troubleshooting.md) | Sintomas, causas prováveis e ações | [en/troubleshooting.md](en/troubleshooting.md) |
| [Privacidade e segurança](privacy.md) | Dados armazenados, serviços externos e cuidados com backups | [en/privacy.md](en/privacy.md) |
| [Atualização](updating.md) | Atualizar, migrar entradas existentes e reverter | [en/updating.md](en/updating.md) |
| [Perguntas frequentes](FAQ.md) | Dúvidas comuns | [en/FAQ.md](en/FAQ.md) |
| [Notas da versão 0.3.0](releases/0.3.0.md) | O que mudou nesta versão | — |
| [Exemplos](../examples/README.md) | Painel nativo e automações opcionais, prontos para adaptar | — |

## Como ler as imagens

As imagens do guia são de três tipos, sempre identificadas na legenda:

- **Captura real:** tela de um painel de exemplo do Home Assistant. Apenas `images/dashboard-overview.png` é captura real.
- **Diagrama ilustrativo:** esquema da arquitetura, do fluxo de cadastro, do ciclo de comando, da recuperação ou das entidades. Não é uma captura de tela.
- **Guia ilustrativo:** descrição de telas feita para orientação. Os nomes de menus e botões podem variar conforme a versão do Home Assistant.

## Escopo

A integração suporta **somente** bombas de calor Fairland / TurboSilence vinculadas a uma conta na nuvem do fabricante. Ela não controla outros controladores Tholz, iluminação, saídas auxiliares ou interruptores. Não há acesso local: sem nuvem, não há controle.

Veja também o [README em português do projeto](../README.pt-BR.md).
