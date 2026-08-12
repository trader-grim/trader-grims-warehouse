{ lib, buildGoModule, fetchurl }:

buildGoModule rec {
  pname = "luet";
  version = "0.9.26";

  # Upstream tag 0.9.26 resolves to this exact commit.  fetchurl verifies the
  # raw source archive; upstream vendors its Go dependency graph.
  src = fetchurl {
    url = "https://github.com/mudler/luet/archive/48f17dbc7a9edb94b1415a2eeeac4e5c2d45f5d3.tar.gz";
    hash = "sha256-wN2VRYsPdF88Cj73ONh7AYTtowjp/X+EtDzOUYTCLCI=";
  };
  vendorHash = null;
  subPackages = [ "." ];
  ldflags = [ "-s" "-w" "-X main.version=${version}" ];

  meta = {
    description = "Container package manager with SAT dependency solver";
    homepage = "https://github.com/mudler/luet";
    license = lib.licenses.gpl3Plus;
    mainProgram = "luet";
  };
}
