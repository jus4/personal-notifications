{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312.withPackages (ps: with ps; [
    fastapi
    uvicorn
    sqlmodel
    pydantic-settings
    httpx
  ]);

  unstable = import (builtins.fetchTarball {
  url = "https://github.com/NixOS/nixpkgs/archive/nixos-unstable.tar.gz";
}) { };
in
pkgs.mkShell {
  packages = [
    python
    pkgs.fastapi-cli
    pkgs.uv
    # Use postgresql_17 from unstable overlay
    (unstable.postgresql_17)
  ];

  shellHook = ''
    echo "Python + FastAPI development environment ready!"
    python --version
    echo "FastAPI environment: $(which python)"
  '';
}
