#!/usr/bin/env bash
# tgw bash/zsh tab completion
# Source this file in ~/.bashrc or ~/.zshrc:
#   source /opt/TGW/src/trader-grims-warehouse/etc/completion/tgw-completion.bash
# Or install system-wide:
#   sudo cp /opt/TGW/src/trader-grims-warehouse/etc/completion/tgw-completion.bash \
#        /etc/bash_completion.d/tgw

_tgw_subcommands=(
    btw
    build-all
    build-archive-index
    build-full
    build-full-csv
    build-locations
    build-search
    build-search-csv
    build-sqlite
    build-thumbnails
    category-groups
    catlocmvall
    data-scrub
    ebay-pull
    ebay-sweep
    ensure-catalog
    get
    get-ebay-token
    health
    hint
    import-sold-csv
    list
    locationupdate
    lookup
    mvitems
    note
    publish
    quality
    requeue
    resolve
    resolve-legacy
    restart-ebay-token
    seo-audit
    serve
    set-template
    setup-ebay-hooks
    sku-migrate
    staged
    status
    statusupdate
    store-categories
    strikethrough-check
    suggest
    suggest-edit
    titleupdate
    todo
    update
    update-where
    velocity-report
    verifiedupdate
)

# Subcommands that take a SKU as their first positional arg
_tgw_sku_cmds=(get hint lookup quality set-template titleupdate verifiedupdate
               locationupdate statusupdate update publish)

# Subcommands that take multiple SKUs
_tgw_multi_sku_cmds=(publish quality statusupdate mvitems)

# Lightweight SKU completion: read first column from search-catalog.json (cached)
_tgw_complete_sku() {
    local catalog=/opt/TGW/data/ItemCatalog/search-catalog.json
    if [[ -f "$catalog" ]]; then
        # Extract SKU values — fast grep rather than full JSON parse
        grep -o '"sku": *"[^"]*"' "$catalog" 2>/dev/null \
            | grep -o 'tgw[0-9]*' \
            | grep "^${COMP_WORDS[COMP_CWORD]}" \
            | head -50
    fi
}

_tgw_complete_location() {
    local loc_tree=/opt/TGW/data/ItemCatalog/location-tree
    if [[ -d "$loc_tree" ]]; then
        # Complete from first-level location dirs
        local cur="${COMP_WORDS[COMP_CWORD]}"
        find "$loc_tree" -maxdepth 2 -mindepth 1 -type d 2>/dev/null \
            | sed "s|$loc_tree/||" \
            | grep "^$cur" \
            | head -30
    fi
}

_tgw() {
    local cur prev words cword
    _init_completion 2>/dev/null || {
        COMPREPLY=()
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    }

    # First positional after 'tgw' — complete subcommands
    if [[ $cword -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${_tgw_subcommands[*]}" -- "$cur") )
        return
    fi

    local subcmd="${words[1]}"

    # Global flags
    if [[ "$cur" == -* ]]; then
        local flags="--config --json"
        case "$subcmd" in
            health|status) flags+=" --no-ollama --no-ebay" ;;
            list)          flags+=" --search --location --status --date-from --date-to --limit" ;;
            requeue)       flags+=" --no-title --unidentified --hint-set --no-draft --no-price --catalog-only --limit --run" ;;
            ebay-sweep)    flags+=" --output --groups" ;;
            import-sold-csv) flags+=" --fuzzy --fuzzy-threshold --dry-run" ;;
            mvitems)       flags+=" --from --search --status --check-only" ;;
            catlocmvall)   flags+=" --check-only" ;;
            suggest-edit)  flags+=" --pending-only" ;;
            category-groups) flags+=" --reseed" ;;
            set-template)  flags+=" --list --camera --dry-run" ;;
            todo)          flags+=" --add --done --priority --source --all --seed" ;;
            sku-migrate)   flags+=" --class --dry-run --run --limit --manifest --check-collisions --include-live-ebay" ;;
            data-scrub)    flags+=" --pass --write" ;;
            velocity-report) flags+=" --refresh --category --min-sold --json --output" ;;
        esac
        COMPREPLY=( $(compgen -W "$flags" -- "$cur") )
        return
    fi

    # Subcommand-specific completions
    case "$subcmd" in
        get|hint|lookup|quality|set-template|titleupdate|verifiedupdate|update|publish)
            if [[ $cword -eq 2 ]]; then
                COMPREPLY=( $(_tgw_complete_sku) )
                return
            fi
            ;;
        statusupdate)
            # statusupdate <value> <sku...>
            if [[ $cword -ge 3 ]]; then
                COMPREPLY=( $(_tgw_complete_sku) )
                return
            fi
            ;;
        mvitems)
            # mvitems <to_location> [skus...]
            if [[ $cword -eq 2 ]]; then
                COMPREPLY=( $(_tgw_complete_location) )
                return
            elif [[ $cword -ge 3 ]] && [[ "$prev" != "--from" ]] && [[ "$prev" != "--search" ]] && [[ "$prev" != "--status" ]]; then
                COMPREPLY=( $(_tgw_complete_sku) )
                return
            fi
            if [[ "$prev" == "--from" ]]; then
                COMPREPLY=( $(_tgw_complete_location) )
                return
            fi
            ;;
        locationupdate)
            if [[ $cword -eq 3 ]]; then
                COMPREPLY=( $(_tgw_complete_location) )
                return
            elif [[ $cword -eq 2 ]]; then
                COMPREPLY=( $(_tgw_complete_sku) )
                return
            fi
            ;;
        catlocmvall)
            if [[ $cword -eq 2 ]] || [[ $cword -eq 3 ]]; then
                COMPREPLY=( $(_tgw_complete_location) )
                return
            fi
            ;;
        todo)
            if [[ $cword -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "claude admin gemini db" -- "$cur") )
                return
            fi
            ;;
    esac

    COMPREPLY=()
}

complete -F _tgw tgw
