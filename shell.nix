{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312.withPackages (ps: with ps; [
    fastapi
    uvicorn
    sqlmodel
  ]);
in
pkgs.mkShell {
  packages = [
    python
    pkgs.fastapi-cli
    pkgs.uv
  ];

  shellHook = ''
    echo "Python + FastAPI development environment ready!"
    python --version
    echo "FastAPI environment: $(which python)"
  '';
}
