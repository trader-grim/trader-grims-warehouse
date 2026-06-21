# TGW inference stack — ollama + whisper.cpp
# Import in any host that runs local AI (production; skip on iMac12,1 — CPU-only)
{ pkgs, ... }:
{
  services.ollama = {
    enable = true;
    # GPU acceleration — auto-detected; falls back to CPU if no supported GPU
    acceleration = null;
  };

  environment.systemPackages = with pkgs; [
    whisper-cpp
  ];
}
