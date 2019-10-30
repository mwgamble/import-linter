import copy
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import networkx

from importlinter.application import output
from importlinter.domain import fields, helpers
from importlinter.domain.contract import Contract, ContractCheck
from importlinter.domain.imports import Module
from importlinter.domain.ports.graph import ImportGraph


class Layer:
    def __init__(self, name: str, is_optional: bool = False) -> None:
        self.name = name
        self.is_optional = is_optional


class LayerField(fields.Field):
    def parse(self, raw_data: Union[str, List]) -> Layer:
        raw_string = fields.StringField().parse(raw_data)
        if raw_string.startswith("(") and raw_string.endswith(")"):
            layer_name = raw_string[1:-1]
            is_optional = True
        else:
            layer_name = raw_string
            is_optional = False
        return Layer(name=layer_name, is_optional=is_optional)


class LayersContract(Contract):
    """
    Defines a 'layered architecture' where there is a unidirectional dependency flow.

    Specifically, higher layers may depend on lower layers, but not the other way around.
    To allow for a repeated pattern of layers across a project, you may also define a set of
    'containers', which are treated as the parent package of the layers.

    Layers are required by default: if a layer is listed in the contract, the contract will be
    broken if the layer doesn’t exist. You can make a layer optional by wrapping it in parentheses.

    Configuration options:

        - layers:         An ordered list of layers. Each layer is the name of a module relative
                          to its parent package. The order is from higher to lower level layers.
        - containers:     A list of the parent Modules of the layers (optional).
        - ignore_imports: A list of DirectImports. These imports will be ignored: if the import
                          would cause a contract to be broken, adding it to the list will cause
                          the contract be kept instead. (Optional.)
    """

    type_name = "layers"

    layers = fields.ListField(subfield=LayerField())
    containers = fields.ListField(subfield=fields.StringField(), required=False)
    ignore_imports = fields.ListField(subfield=fields.DirectImportField(), required=False)

    def check(self, graph: ImportGraph) -> ContractCheck:
        is_kept = True
        self._call_count = 0
        direct_imports_to_ignore = self.ignore_imports if self.ignore_imports else []
        removed_imports = helpers.pop_imports(
            graph, direct_imports_to_ignore  # type: ignore
        )

        # print(f"Beginning squashing at {datetime.now().time()}...")
        # for container in self.containers:
        #     for layer in self.layers:
        #         module = self._module_from_layer(layer, container)
        #         self._squash_package(package_name=module.name, graph=temp_graph)temp_graph
        # print(f"Finished squashing at {datetime.now().time()}.")

        print(f"Beginning high level layer analysis at {datetime.now().time()}...")
        high_level_invalid_chains = []
        for higher_layer_package, lower_layer_package in self._generate_module_permutations(graph):
            temp_graph = copy.deepcopy(graph)
            self._squash_package(package_name=lower_layer_package.name, graph=temp_graph)
            self._squash_package(package_name=higher_layer_package.name, graph=temp_graph)
            # Delete all other layers.
            for layer in self.layers:
                module = self._module_from_layer(layer, self.containers[0])
                if module not in (higher_layer_package, lower_layer_package):
                    self._remove_package(package_name=module.name, graph=temp_graph)

            try:
                layer_chain_data = list(
                    networkx.all_shortest_paths(
                        temp_graph._networkx_graph,
                        source=lower_layer_package.name,
                        target=higher_layer_package.name,
                    )
                )
            except networkx.exception.NetworkXNoPath:
                layer_chain_data = []

            print(f"Added {len(layer_chain_data)}.")
            if layer_chain_data:
                is_kept = False
                high_level_invalid_chains.append(layer_chain_data)

        print(
            f"Finished high level layer analysis at {datetime.now().time()}. {self._call_count} calls made."
        )

        print(f"Beginning getting specific chains at {datetime.now().time()}...")
        import ipdb; ipdb.set_trace()
        invalid_chains = self._determine_specific_chains(high_level_invalid_chains, graph)
        print(f"Finished getting specific chains at {datetime.now().time()}.")

        return ContractCheck(kept=is_kept, metadata={"invalid_chains": invalid_chains})

    def _pop_imports_outside_layer_by_module(self, graph, layer_package, module_names):
        imports_by_layer = {}
        for module_name in module_names:
            imports_by_layer[module_name] = []
            for imported_module in graph.find_modules_directly_imported_by(module_name):
                if not Module(imported_module).is_descendant_of(layer_package):
                    import_details = graph.get_import_details(
                        importer=module_name, imported=imported_module
                    )
                    imports_by_layer[module_name].extend(import_details)
            graph.remove_module(module_name)
        return imports_by_layer

    def _squash_package(self, package_name, graph):
        modules_that_import_package = set()
        modules_imported_by_package = set()

        package_module = Module(package_name)
        package_modules = {package_name} | graph.find_descendants(package_name)

        # Remove all the modules from the package, keeping track of the imports to and from.
        for package_module_name in package_modules:
            for module in graph.find_modules_directly_imported_by(package_module_name):
                if not Module(module).is_descendant_of(package_module):
                    modules_imported_by_package.add(module)
            for module in graph.find_modules_that_directly_import(package_module_name):
                if not Module(module).is_descendant_of(package_module):
                    modules_that_import_package.add(module)
            graph.remove_module(package_module_name)

        # Now add a single module back.

        graph.add_module(package_name, is_squashed=True)

        for module in modules_that_import_package:
            graph.add_import(importer=module, imported=package_name)
        for module in modules_imported_by_package:
            graph.add_import(importer=package_name, imported=module)

    def _remove_package(self, package_name, graph):
        package_modules = {Module(package_name)} | graph.find_descendants(package_name)
        for package_module_name in package_modules:
            graph.remove_module(package_module_name)

    def _determine_specific_chains(
        self, high_level_invalid_chains: List[Tuple[str, ...]], graph: ImportGraph
    ) -> List[Dict[str, Any]]:
        invalid_chains = []
        for high_level_invalid_chain in high_level_invalid_chains:
            if len(high_level_invalid_chain) == 2:
                # TODO
                invalid_chains.append(high_level_invalid_chain)
            else:
                # import ipdb; ipdb.set_trace()
                starting_package = high_level_invalid_chain[0]
                candidate_starting_modules = graph.find_modules_that_directly_import(
                    high_level_invalid_chain[1]
                )
                starting_modules = [
                    m
                    for m in candidate_starting_modules
                    if Module(m).is_descendant_of(starting_package)
                ]
                ending_package = high_level_invalid_chain[-1]
                candidate_ending_modules = graph.find_modules_directly_imported_by(
                    high_level_invalid_chain[-2]
                )
                ending_modules = [
                    m
                    for m in candidate_ending_modules
                    if Module(m).is_descendant_of(ending_package)
                ]
                for starting_module in starting_modules:
                    invalid_chains.append(
                        [starting_module] + high_level_invalid_chain[1:-1] + [ending_modules[0]]
                    )
                for ending_module in ending_modules[1:]:
                    invalid_chains.append(
                        [starting_modules[0]] + high_level_invalid_chain[1:-1] + [ending_module]
                    )
        return self._convert_chains(invalid_chains, graph)

    def _convert_chains(self, invalid_chains, graph):
        converted_chains = []
        for chain in invalid_chains:
            converted_chain = []

            for importer, imported in [(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]:
                import_details = graph.get_import_details(importer=importer, imported=imported)
                line_numbers = tuple(j["line_number"] for j in import_details)
                converted_chain.append(
                    {"importer": importer, "imported": imported, "line_numbers": line_numbers}
                )
            converted_chains.append(converted_chain)

        return converted_chains

    def _alt_check(self, graph: ImportGraph) -> ContractCheck:
        is_kept = True
        invalid_chains = []

        direct_imports_to_ignore = self.ignore_imports if self.ignore_imports else []
        removed_imports = helpers.pop_imports(
            graph, direct_imports_to_ignore  # type: ignore
        )

        if self.containers:
            self._validate_containers(graph)
        else:
            self._check_all_containerless_layers_exist(graph)
        self._call_count = 0

        print(f"Beginning layer analysis at {datetime.now().time()}...")
        for higher_layer_package, lower_layer_package in self._generate_module_permutations(graph):
            layer_chain_data = self._build_layer_chain_data(
                higher_layer_package=higher_layer_package,
                lower_layer_package=lower_layer_package,
                graph=graph,
            )

            if layer_chain_data["chains"]:
                is_kept = False
                invalid_chains.append(layer_chain_data)
        print(
            f"Finished layer analysis at {datetime.now().time()}. {self._call_count} calls made."
        )

        helpers.add_imports(graph, removed_imports)

        return ContractCheck(kept=is_kept, metadata={"invalid_chains": invalid_chains})

    def render_broken_contract(self, check: ContractCheck) -> None:
        for chain in check.metadata["invalid_chains"]:

            first_line = True
            for direct_import in chain:
                importer, imported = (direct_import["importer"], direct_import["imported"])
                line_numbers = ", ".join(f"l.{n}" for n in direct_import["line_numbers"])
                import_string = f"{importer} -> {imported} ({line_numbers})"
                if first_line:
                    output.print_error(f"-   {import_string}", bold=False)
                    first_line = False
                else:
                    output.indent_cursor()
                    output.print_error(import_string, bold=False)

            output.new_line()

    def old_render_broken_contract(self, check: ContractCheck) -> None:
        for chains_data in check.metadata["invalid_chains"]:
            higher_layer, lower_layer = (chains_data["higher_layer"], chains_data["lower_layer"])
            output.print(f"{lower_layer} is not allowed to import {higher_layer}:")
            output.new_line()

            for chain in chains_data["chains"]:
                first_line = True
                for direct_import in chain:
                    importer, imported = (direct_import["importer"], direct_import["imported"])
                    line_numbers = ", ".join(f"l.{n}" for n in direct_import["line_numbers"])
                    import_string = f"{importer} -> {imported} ({line_numbers})"
                    if first_line:
                        output.print_error(f"-   {import_string}", bold=False)
                        first_line = False
                    else:
                        output.indent_cursor()
                        output.print_error(import_string, bold=False)
                output.new_line()

            output.new_line()

    def _validate_containers(self, graph: ImportGraph) -> None:
        root_package_names = self.session_options["root_packages"]
        for container in self.containers:  # type: ignore
            if Module(container).root_package_name not in root_package_names:
                if len(root_package_names) == 1:
                    root_package_name = root_package_names[0]
                    error_message = (
                        f"Invalid container '{container}': a container must either be a "
                        f"subpackage of {root_package_name}, or {root_package_name} itself."
                    )
                else:
                    packages_string = ", ".join(root_package_names)
                    error_message = (
                        f"Invalid container '{container}': a container must either be a root "
                        f"package, or a subpackage of one of them. "
                        f"(The root packages are: {packages_string}.)"
                    )
                raise ValueError(error_message)
            self._check_all_layers_exist_for_container(container, graph)

    def _check_all_layers_exist_for_container(self, container: str, graph: ImportGraph) -> None:
        for layer in self.layers:  # type: ignore
            if layer.is_optional:
                continue
            layer_module_name = ".".join([container, layer.name])
            if layer_module_name not in graph.modules:
                raise ValueError(
                    f"Missing layer in container '{container}': "
                    f"module {layer_module_name} does not exist."
                )

    def _check_all_containerless_layers_exist(self, graph: ImportGraph) -> None:
        for layer in self.layers:  # type: ignore
            if layer.is_optional:
                continue
            if layer.name not in graph.modules:
                raise ValueError(
                    f"Missing layer '{layer.name}': module {layer.name} does not exist."
                )

    def _generate_module_permutations(self, graph: ImportGraph) -> Iterator[Tuple[Module, Module]]:
        """
        Return all possible combinations of higher level and lower level modules, in pairs.

        Each pair of modules consists of immediate children of two different layers. The first
        module is in a layer higher than the layer of the second module. This means the first
        module is allowed to import the second, but not the other way around.

        Returns:
            module_in_higher_layer, module_in_lower_layer
        """
        # If there are no containers, we still want to run the loop once.
        quasi_containers = self.containers or [None]

        for container in quasi_containers:  # type: ignore
            for index, higher_layer in enumerate(self.layers):  # type: ignore
                higher_layer_module = self._module_from_layer(higher_layer, container)

                if higher_layer_module.name not in graph.modules:
                    continue

                for lower_layer in self.layers[index + 1 :]:  # type: ignore

                    lower_layer_module = self._module_from_layer(lower_layer, container)

                    if lower_layer_module.name not in graph.modules:
                        continue

                    yield higher_layer_module, lower_layer_module

    def _module_from_layer(self, layer: Layer, container: Optional[str] = None) -> Module:
        if container:
            name = ".".join([container, layer.name])
        else:
            name = layer.name
        return Module(name)

    def _build_layer_chain_data(
        self, higher_layer_package: Module, lower_layer_package: Module, graph: ImportGraph
    ) -> Dict[str, Any]:
        layer_chain_data = {
            "higher_layer": higher_layer_package.name,
            "lower_layer": lower_layer_package.name,
            "chains": [],
        }
        assert isinstance(layer_chain_data["chains"], list)  # For type checker.
        self._call_count += 1
        chains = graph.find_shortest_chains(
            importer=lower_layer_package.name, imported=higher_layer_package.name
        )
        if chains:
            for chain in chains:
                chain_data = []
                for importer, imported in [
                    (chain[i], chain[i + 1]) for i in range(len(chain) - 1)
                ]:
                    import_details = graph.get_import_details(importer=importer, imported=imported)
                    line_numbers = tuple(j["line_number"] for j in import_details)
                    chain_data.append(
                        {"importer": importer, "imported": imported, "line_numbers": line_numbers}
                    )

                layer_chain_data["chains"].append(chain_data)
        return layer_chain_data
