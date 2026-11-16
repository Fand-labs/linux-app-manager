# Linux App Manager

Aplicativo gráfico para Linux desenvolvido em Python, GTK 4 e Libadwaita. Permite visualizar e gerenciar aplicativos instalados por meio de uma interface simples e intuitiva.

## Funcionalidades

- Listagem de aplicativos instalados.
- Suporte a APT, Flatpak e Snap.
- Pesquisa de aplicativos.
- Executar e encerrar aplicativos.
- Criar atalhos na área de trabalho.
- Desinstalar aplicativos individualmente ou em lote.
- Exportar a lista de aplicativos para CSV.
- Registro de erros e atividades em log.

## Requisitos

- Linux com suporte a GTK 4 e Libadwaita.
- Python 3.
- PyGObject.
- PolicyKit.
- APT, Flatpak ou Snap.

## Instalação

### Executar pelo código-fonte

Instale as dependências em distribuições Debian e derivadas:

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 polkitd
```

Execute:

```bash
python3 App.py
```

### Gerar pacote DEB

Instale o `dpkg-dev`:

```bash
sudo apt install dpkg-dev
```

Gere o pacote:

```bash
python3 build_deb.py
```

Instale o arquivo `.deb` gerado:

```bash
sudo apt install ./linuxappmanager_1.0.0_all.deb
```

## Estrutura do projeto

```text
linux-app-manager/
├── App.py
├── build_deb.py
├── icon.png
└── README.md
```

## Compatibilidade

Desenvolvido para ambientes Linux com GTK 4 e Libadwaita. Atualmente, oferece suporte ao gerenciamento de pacotes APT, Flatpak e Snap.

O instalador DEB é destinado a distribuições Debian e derivadas.

## Contribuições

Sugestões, correções e melhorias são bem-vindas. Abra uma *Issue* ou envie um *Pull Request*.

## Licença

Consulte o arquivo `LICENSE` para informações sobre os termos de uso e distribuição.
