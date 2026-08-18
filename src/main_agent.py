from architect_agent import ArchitectAgent

arquiteto = ArchitectAgent()
resultado = arquiteto.run("Need 3 blue and red cards to deal global damage")

print(resultado["messages"][-1].content)