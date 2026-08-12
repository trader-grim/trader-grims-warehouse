{ stdenv, openssl }:

stdenv.mkDerivation {
  pname = "tgw-a3-platform-bootstrap";
  version = "1";
  buildInputs = [ openssl ];
  dontUnpack = true;
  dontConfigure = true;
  buildPhase = ''
    runHook preBuild
    $CC -Wall -Wextra -Werror \
      -DTGW_RENDER_WRAPPER_CONFIG='"/etc/tgw/nix-observer-render-wrapper.conf"' \
      -o tgw-nix-observer-render-wrapper \
      ${../src/native/tgw_nix_observer_render_transport.c} -lcrypto
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    install -Dm0555 tgw-nix-observer-render-wrapper \
      $out/bin/tgw-nix-observer-render-wrapper
    install -Dm0444 ${../src/tgw/nix_observer_render_remote.py} \
      $out/libexec/tgw/nix-observer-render-remote.py
    install -Dm0444 ${../src/tgw/nix_observer_render_helper.py} \
      $out/libexec/tgw/nix-observer-render-helper.py
    runHook postInstall
  '';
}
